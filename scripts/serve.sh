#!/usr/bin/env bash
#
# serve.sh — Unified DeerFlow service launcher
#
# Usage:
#   ./scripts/serve.sh [--dev|--prod] [--gateway-reload] [--daemon] [--stop|--restart]
#
# Modes:
#   --dev       Development mode with stable Gateway and frontend hot-reload (default)
#   --prod      Production mode, pre-built frontend, no hot-reload
#   --gateway-reload  Enable Gateway hot-reload in dev mode (off by default so
#               command-room/subagent runs are not killed when backend files change)
#   --daemon    Run all services in background (nohup), exit after startup
#
# Actions:
#   --skip-install  Skip dependency installation (faster restart)
#   --stop      Stop all running services and exit
#   --restart   Stop all services, then start with the given mode flags
#
# Examples:
#   ./scripts/serve.sh --dev                 # Stable Gateway dev, frontend hot reload
#   ./scripts/serve.sh --dev --gateway-reload # Gateway dev with backend hot reload
#   ./scripts/serve.sh --prod                # Gateway prod
#   ./scripts/serve.sh --dev --daemon        # Gateway dev, background
#   ./scripts/serve.sh --stop                # Stop all services
#   ./scripts/serve.sh --restart --dev       # Restart dev services
#
# Must be run from the repo root directory.

set -e

REPO_ROOT="$(builtin cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
cd "$REPO_ROOT"

NGINX_TEMPLATE_CONFIG="$REPO_ROOT/docker/nginx/nginx.local.conf"
NGINX_RUNTIME_CONFIG="$REPO_ROOT/logs/nginx.local.generated.conf"

# ── Load .env ────────────────────────────────────────────────────────────────

if [ -f "$REPO_ROOT/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        key=${line%%=*}
        value=${line#*=}
        [ "$line" != "$key" ] || continue
        if ! printf '%s\n' "$key" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'; then
            continue
        fi
        case "$value" in
            \"*\") value=${value#\"}; value=${value%\"} ;;
            \'*\') value=${value#\'}; value=${value%\'} ;;
        esac
        export "$key=$value"
    done < "$REPO_ROOT/.env"
fi

_pick_python() {
    local candidate
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info.major >= 3 else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# ── Argument parsing ─────────────────────────────────────────────────────────

DEV_MODE=true
GATEWAY_RELOAD=false
DAEMON_MODE=false
SKIP_INSTALL=false
ACTION="start"   # start | stop | restart

for arg in "$@"; do
    case "$arg" in
        --dev)     DEV_MODE=true ;;
        --prod)    DEV_MODE=false ;;
        --gateway-reload) GATEWAY_RELOAD=true ;;
        --daemon)  DAEMON_MODE=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --stop)    ACTION="stop" ;;
        --restart) ACTION="restart" ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--dev|--prod] [--gateway-reload] [--daemon] [--skip-install] [--stop|--restart]"
            exit 1
            ;;
    esac
done

GATEWAY_PORT="${DEER_FLOW_GATEWAY_PORT:-8001}"
FRONTEND_PORT="${DEER_FLOW_FRONTEND_PORT:-3000}"
NGINX_PORT="${DEER_FLOW_NGINX_PORT:-2026}"
GATEWAY_HOST="${DEER_FLOW_BIND_HOST:-${DEER_FLOW_GATEWAY_HOST:-127.0.0.1}}"

_validate_port_value() {
    local name=$1 value=$2

    case "$value" in
        ''|*[!0-9]*)
            echo "✗ $name must be a numeric TCP port: $value"
            exit 1
            ;;
    esac

    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        echo "✗ $name must be between 1 and 65535: $value"
        exit 1
    fi
}

_validate_port_value DEER_FLOW_GATEWAY_PORT "$GATEWAY_PORT"
_validate_port_value DEER_FLOW_FRONTEND_PORT "$FRONTEND_PORT"
_validate_port_value DEER_FLOW_NGINX_PORT "$NGINX_PORT"

# ── Stop helper ──────────────────────────────────────────────────────────────

# Every deer-flow worktree (the main checkout + each linked worktree) defaults
# to the same dev ports, so a service started from ANY of them must be
# reclaimable from here — otherwise `make stop`/`make dev` in this worktree can
# neither kill nor take over a port held by a sibling worktree.
# DEERFLOW_ROOTS is that set of roots; processes living outside all of them
# (e.g. an unrelated project on port 3000) are still never touched.
# Sorted most-specific-first (longest path first): a linked worktree lives
# under the main checkout, so both roots are substrings of its files — checking
# the deeper root first attributes a reclaimed port to the right worktree.
DEERFLOW_ROOTS="$(
    {
        printf '%s\n' "$REPO_ROOT"
        git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null |
            awk '/^worktree /{print $2}'
    } | awk 'NF && !seen[$0]++ {print length($0)"\t"$0}' | sort -rn | sed 's/^[0-9]*\t//'
)"

# True if PID has an open file/cwd under any deer-flow worktree root. The
# trailing slash keeps a sibling dir like ".../deer-flow-notes" from matching
# the ".../deer-flow" root.
_is_deerflow_pid() {
    local pid=$1 files root

    # Daemon children inherit DEERFLOW_DAEMON_ROOT from run_service. Checking
    # it (Linux only — macOS has no /proc) identifies processes like
    # next-server that lsof misses, so the name/port reaps in stop_all can
    # claim them.
    if [ -r "/proc/$pid/environ" ] &&
        tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -Fxq "DEERFLOW_DAEMON_ROOT=$REPO_ROOT"; then
        return 0
    fi

    files=$(lsof -b -w -p "$pid" 2>/dev/null) || return 1
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        case "$files" in
            *"$root"/*) return 0 ;;
        esac
    done <<< "$DEERFLOW_ROOTS"
    return 1
}

# Report ports about to be reclaimed from a *different* worktree, so stopping
# (or starting, which stops first) isn't silently killing someone else's run.
_report_reclaimed_ports() {
    local port pid files root owner
    for port in "$GATEWAY_PORT" "$FRONTEND_PORT" "$NGINX_PORT"; do
        for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null); do
            _is_deerflow_pid "$pid" || continue
            files=$(lsof -b -w -p "$pid" 2>/dev/null)
            case "$files" in *"$REPO_ROOT"/*) continue ;; esac  # this worktree — normal
            owner=""
            while IFS= read -r root; do
                [ -n "$root" ] || continue
                case "$files" in *"$root"/*) owner="$root"; break ;; esac
            done <<< "$DEERFLOW_ROOTS"
            echo "  ↻ Reclaiming port $port from another worktree: ${owner:-?}"
            break
        done
    done
}

_kill_repo_processes() {
    local pattern=$1
    local pid
    local pids=""

    while IFS= read -r pid; do
        if [ -n "$pid" ] && _is_deerflow_pid "$pid"; then
            case " $pids " in
                *" $pid "*) ;;
                *) pids="$pids $pid" ;;
            esac
        fi
    done < <(pgrep -f "$pattern" 2>/dev/null || true)

    if [ -n "$pids" ]; then
        kill $pids 2>/dev/null || true
    fi
}

_kill_repo_port() {
    local port=$1
    local pid
    local pids=""

    while IFS= read -r pid; do
        if [ -n "$pid" ] && _is_deerflow_pid "$pid"; then
            case " $pids " in
                *" $pid "*) ;;
                *) pids="$pids $pid" ;;
            esac
        fi
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)

    if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
    fi
}

_is_port_listening() {
    local port=$1

    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    if command -v ss >/dev/null 2>&1; then
        if ss -ltn "( sport = :$port )" 2>/dev/null | tail -n +2 | grep -q .; then
            return 0
        fi
    fi

    if command -v netstat >/dev/null 2>&1; then
        # macOS netstat can include non-listening TCP states even with -ltn.
        # Only treat explicit LISTEN rows as occupied.
        if netstat -ltn 2>/dev/null | awk -v port="$port" '$NF == "LISTEN" && $4 ~ "(^|[.:])" port "$" { found=1 } END { exit(found ? 0 : 1) }'; then
            return 0
        fi
    fi

    return 1
}

_pick_available_port() {
    local port=$1

    while [ "$port" -le 65535 ]; do
        if ! _is_port_listening "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
        port=$((port + 1))
    done

    return 1
}

_resolve_frontend_port() {
    local requested_port="$FRONTEND_PORT"

    if [ -n "${DEER_FLOW_FRONTEND_PORT:-}" ]; then
        return 0
    fi

    if ! _is_port_listening "$requested_port"; then
        return 0
    fi

    FRONTEND_PORT="$(_pick_available_port 6001)" || {
        echo "✗ Frontend port $requested_port is busy and no free fallback port was found."
        exit 1
    }

    echo "  • Frontend port $requested_port is busy outside this run; using localhost:$FRONTEND_PORT."
}

render_nginx_config() {
    mkdir -p logs
    sed \
        -e "s/127\.0\.0\.1:8001/127.0.0.1:${GATEWAY_PORT}/g" \
        -e "s/127\.0\.0\.1:3000/127.0.0.1:${FRONTEND_PORT}/g" \
        -e "s/listen 2026;/listen ${NGINX_PORT};/g" \
        -e "s/listen \[::\]:2026;/listen [::]:${NGINX_PORT};/g" \
        "$NGINX_TEMPLATE_CONFIG" > "$NGINX_RUNTIME_CONFIG"
}

_is_repo_nginx_pid() {
    local pid=$1
    local command
    local args

    command=$(ps -p "$pid" -o comm= 2>/dev/null) || return 1
    case "$command" in
        nginx|*/nginx) ;;
        *) return 1 ;;
    esac

    args=$(ps -p "$pid" -o args= 2>/dev/null) || return 1
    local root
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        case "$args" in
            *"$root"/docker/nginx/nginx.local.conf*|*"$root"/*) return 0 ;;
        esac
    done <<< "$DEERFLOW_ROOTS"

    _is_deerflow_pid "$pid"
}

_kill_repo_nginx() {
    local pid
    local pids=""

    if [ -f "$REPO_ROOT/logs/nginx.pid" ]; then
        read -r pid < "$REPO_ROOT/logs/nginx.pid" || true
        if [ -n "$pid" ] && _is_repo_nginx_pid "$pid"; then
            pids="$pids $pid"
        fi
    fi

    while IFS= read -r pid; do
        if [ -n "$pid" ] && _is_repo_nginx_pid "$pid"; then
            case " $pids " in
                *" $pid "*) ;;
                *) pids="$pids $pid" ;;
            esac
        fi
    done < <(pgrep -f nginx 2>/dev/null || true)

    if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
    fi
}

stop_all() {
    echo "Stopping all services..."
    _report_reclaimed_ports
    _kill_repo_processes "uvicorn app.gateway.app:app"
    _kill_repo_processes "next dev"
    _kill_repo_processes "next start"
    _kill_repo_processes "next-server"
    if [ -f "$NGINX_RUNTIME_CONFIG" ]; then
        nginx -c "$NGINX_RUNTIME_CONFIG" -p "$REPO_ROOT" -s quit 2>/dev/null || true
    fi
    nginx -c "$NGINX_TEMPLATE_CONFIG" -p "$REPO_ROOT" -s quit 2>/dev/null || true
    sleep 1
    _kill_repo_nginx
    # Force-kill any survivors still holding the service ports. The nginx port
    # is included so a lingering nginx (or any deer-flow process) that
    # _kill_repo_nginx did not match by name still gets reclaimed — otherwise
    # `make dev` fails its nginx port preflight.
    _kill_repo_port "$GATEWAY_PORT"
    _kill_repo_port "$FRONTEND_PORT"
    _kill_repo_port "$NGINX_PORT"
    ./scripts/cleanup-containers.sh deer-flow-sandbox 2>/dev/null || true
    echo "✓ All services stopped"
}

_start_daemon_command() {
    local cmd="$1"
    local python_bin

    if python_bin="$(_pick_python)"; then
        DEERFLOW_DAEMON_ROOT="$REPO_ROOT" "$python_bin" - "$cmd" <<'PY'
import os
import subprocess
import sys

subprocess.Popen(
    sys.argv[1],
    shell=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env=os.environ.copy(),
    start_new_session=True,
)
PY
    else
        nohup env DEERFLOW_DAEMON_ROOT="$REPO_ROOT" sh -c "$cmd" </dev/null > /dev/null 2>&1 &
        disown "$!" 2>/dev/null || true
    fi
}

_start_detached_relauncher() {
    local python_bin

    python_bin="$(_pick_python)" || return 1
    DEERFLOW_DAEMON_ROOT="$REPO_ROOT" DEERFLOW_RELAUNCHER=1 "$python_bin" - "$REPO_ROOT/scripts/serve.sh" "$@" <<'PY'
import os
import subprocess
import sys

subprocess.Popen(
    sys.argv[1:],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env=os.environ.copy(),
    start_new_session=True,
)
PY
}

# ── Action routing ───────────────────────────────────────────────────────────

if [ "$ACTION" = "stop" ]; then
    stop_all
    exit 0
fi

if [ "$ACTION" = "restart" ] && $DAEMON_MODE && [ "${DEERFLOW_RELAUNCHER:-}" != "1" ] && [ "${DEERFLOW_DAEMON_ROOT:-}" = "$REPO_ROOT" ]; then
    _start_detached_relauncher "$@" || {
        echo "✗ Failed to hand off restart to detached launcher."
        exit 1
    }
    echo "✓ Restart handed off to detached launcher"
    exit 0
fi

ALREADY_STOPPED=false
if [ "$ACTION" = "restart" ]; then
    stop_all
    sleep 1
    ALREADY_STOPPED=true
fi

# Mode label for banner
if $DEV_MODE; then
    if $GATEWAY_RELOAD; then
        MODE_LABEL="DEV (Gateway runtime, backend hot-reload enabled)"
    else
        MODE_LABEL="DEV (Gateway runtime, stable backend; frontend hot-reload enabled)"
    fi
else
    MODE_LABEL="PROD (Gateway runtime, optimized)"
fi

if $DAEMON_MODE; then
    MODE_LABEL="$MODE_LABEL [daemon]"
fi

# Runtime path defaults. Local `make dev` launches Gateway from `backend/`,
# so pin DeerFlow-owned state to the expected backend runtime directory and
# create it before uvicorn builds its reload exclude filter.
if [ -z "$DEER_FLOW_PROJECT_ROOT" ]; then
    export DEER_FLOW_PROJECT_ROOT="$REPO_ROOT"
fi

BACKEND_RUNTIME_HOME="$REPO_ROOT/backend/.deer-flow"
if [ -z "$DEER_FLOW_HOME" ]; then
    export DEER_FLOW_HOME="$BACKEND_RUNTIME_HOME"
fi

# `backend/sandbox` is excluded from uvicorn's reload watcher below. uvicorn only
# excludes an absolute path directly when it already exists as a directory;
# otherwise it globs the pattern, and Python 3.12's pathlib rejects absolute glob
# patterns with NotImplementedError, crashing `make dev` on a fresh checkout
# (#3459 / #3454). Creating it here keeps every absolute exclude on the is_dir path.
mkdir -p "$DEER_FLOW_HOME" "$BACKEND_RUNTIME_HOME" "$REPO_ROOT/backend/sandbox"
DEER_FLOW_HOME="$(cd "$DEER_FLOW_HOME" && pwd -P)"
BACKEND_RUNTIME_HOME="$(cd "$BACKEND_RUNTIME_HOME" && pwd -P)"
export DEER_FLOW_HOME

# Extra flags for uvicorn. Gateway reload is opt-in because Command Room can
# edit backend files while it is running; auto-reload would cancel the parent
# run and every active subtask.
if $DEV_MODE && $GATEWAY_RELOAD && ! $DAEMON_MODE; then
    GATEWAY_EXTRA_FLAGS="--reload --timeout-graceful-shutdown 5 --reload-include='*.yaml' --reload-include='.env' --reload-exclude='*.pyc' --reload-exclude='__pycache__' --reload-exclude='$REPO_ROOT/backend/sandbox' --reload-exclude='$DEER_FLOW_HOME' --reload-exclude='$BACKEND_RUNTIME_HOME'"
else
    GATEWAY_EXTRA_FLAGS=""
fi

# ── Stop existing services (skip if restart already did it) ──────────────────

if ! $ALREADY_STOPPED; then
    stop_all
    sleep 1
fi

_resolve_frontend_port

# Frontend command. Next.js honors PORT for both `next dev` and `next start`.
if $DEV_MODE; then
    FRONTEND_CMD="env PORT=$FRONTEND_PORT pnpm run dev"
else
    if ! PYTHON_BIN="$(_pick_python)"; then
        echo "Python is required to generate BETTER_AUTH_SECRET."
        exit 1
    fi
    FRONTEND_CMD="env PORT=$FRONTEND_PORT BETTER_AUTH_SECRET=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(16))') pnpm run preview"
fi

# ── Config check ─────────────────────────────────────────────────────────────

if ! { \
        [ -n "$DEER_FLOW_CONFIG_PATH" ] && [ -f "$DEER_FLOW_CONFIG_PATH" ] || \
        [ -f backend/config.yaml ] || \
        [ -f config.yaml ]; \
    }; then
    echo "✗ No DeerFlow config file found."
    echo "  Run 'make setup' (recommended) or 'make config' to generate config.yaml."
    exit 1
fi

"$REPO_ROOT/scripts/config-upgrade.sh"

# ── Install dependencies ────────────────────────────────────────────────────

# Pick a runnable Python for the extras detector. On Windows/Git Bash,
# `python3` can resolve to the Microsoft Store alias in WindowsApps, which is
# present on PATH but not executable from Bash.
DETECT_PYTHON="$(_pick_python || true)"

# Resolve uv extras (postgres, etc.) from UV_EXTRAS or config.yaml so that
# `uv sync` does not wipe out optional dependencies on every restart. See
# scripts/detect_uv_extras.py and Issue #2754 for context. The detector
# whitelists extra names against `^[A-Za-z][A-Za-z0-9_-]*$`, so the unquoted
# splat below only sees valid uv argument tokens.
#
# Stderr is intentionally NOT redirected so the user sees:
#   - whitelist warnings (e.g. "ignoring invalid UV_EXTRAS entry ';'");
#   - detector crashes (e.g. unexpected Python error).
# `|| true` keeps `set -e` from killing dev startup on a detector failure;
# the result is just an empty UV_EXTRAS_FLAGS, which means "no extras".
UV_EXTRAS_FLAGS=""
if [ -n "$DETECT_PYTHON" ]; then
    UV_EXTRAS_FLAGS=$("$DETECT_PYTHON" "$REPO_ROOT/scripts/detect_uv_extras.py" || { echo "[serve.sh] detect_uv_extras.py failed (exit $?) — proceeding without extras" >&2; echo ""; })
fi

if ! $SKIP_INSTALL; then
    echo "Syncing dependencies..."
    if [ -n "$UV_EXTRAS_FLAGS" ]; then
        echo "  • uv extras: $UV_EXTRAS_FLAGS"
    fi
    # `--all-packages` propagates extras into workspace members (deerflow-harness
    # in particular). Required for postgres extras — see PR #2584.
    # Intentionally unquoted to splat multiple `--extra X` pairs.
    (cd backend && uv sync --quiet --all-packages $UV_EXTRAS_FLAGS) || { echo "✗ Backend dependency install failed"; exit 1; }
    (cd frontend && pnpm install --silent) || { echo "✗ Frontend dependency install failed"; exit 1; }
    echo "✓ Dependencies synced"
else
    echo "⏩ Skipping dependency install (--skip-install)"
fi

# ── Banner ───────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Starting DeerFlow"
echo "=========================================="
echo ""
echo "  Mode: $MODE_LABEL"
echo ""
echo "  Services:"
echo "    Gateway     → ${GATEWAY_HOST}:$GATEWAY_PORT  (REST API + agent runtime; local by default)"
echo "    Frontend    → localhost:$FRONTEND_PORT  (Next.js)"
echo "    Nginx       → localhost:$NGINX_PORT  (reverse proxy)"
echo ""

# ── Cleanup handler ──────────────────────────────────────────────────────────

cleanup() {
    local status="${1:-0}"
    trap - INT TERM
    echo ""
    stop_all
    exit "$status"
}

trap 'cleanup 130' INT
trap 'cleanup 143' TERM

# run_service NAME COMMAND PORT TIMEOUT
# In daemon mode, wraps with nohup. Waits for port to be ready.
run_service() {
    local name="$1" cmd="$2" port="$3" timeout="$4"

    if _is_port_listening "$port"; then
        echo "✗ $name cannot start because port $port is already in use."
        echo "  If it belongs to this worktree, run 'make stop'; otherwise free the port manually."
        cleanup 1
    fi

    echo "Starting $name..."
    if $DAEMON_MODE; then
        _start_daemon_command "$cmd"
    else
        sh -c "$cmd" &
    fi

    ./scripts/wait-for-port.sh "$port" "$timeout" "$name" || {
        local logfile="logs/$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-').log"
        echo "✗ $name failed to start."
        [ -f "$logfile" ] && tail -20 "$logfile"
        cleanup 1
    }
    echo "✓ $name started on localhost:$port"
}

# ── Start services ───────────────────────────────────────────────────────────

mkdir -p logs
mkdir -p temp/client_body_temp temp/proxy_temp temp/fastcgi_temp temp/uwsgi_temp temp/scgi_temp
render_nginx_config

# 1. Gateway API
run_service "Gateway" \
    "cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host '$GATEWAY_HOST' --port $GATEWAY_PORT $GATEWAY_EXTRA_FLAGS > ../logs/gateway.log 2>&1" \
    "$GATEWAY_PORT" 30

# 2. Frontend
run_service "Frontend" \
    "cd frontend && $FRONTEND_CMD > ../logs/frontend.log 2>&1" \
    "$FRONTEND_PORT" 120

# 3. Nginx
run_service "Nginx" \
    "nginx -g 'daemon off;' -c '$NGINX_RUNTIME_CONFIG' -p '$REPO_ROOT' > logs/nginx.log 2>&1" \
    "$NGINX_PORT" 10

# ── Ready ────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  ✓ DeerFlow is running!  [$MODE_LABEL]"
echo "=========================================="
echo ""
echo "  🌐 http://localhost:$NGINX_PORT"
echo ""
echo "  Routing: Frontend → Nginx → Gateway"
echo "  API:     /api/langgraph/*  →  Gateway agent runtime"
echo "           /api/*              →  Gateway REST API ($GATEWAY_PORT)"
echo ""
echo "  📋 Logs: logs/{gateway,frontend,nginx}.log"
echo ""

if $DAEMON_MODE; then
    echo "  🛑 Stop: make stop"
    # Detach — trap is no longer needed
    trap - INT TERM
else
    echo "  Press Ctrl+C to stop all services"
    wait
fi
