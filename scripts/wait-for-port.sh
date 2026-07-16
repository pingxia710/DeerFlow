#!/usr/bin/env bash
#
# wait-for-port.sh - Wait for a TCP port to become available
#
# Usage: ./scripts/wait-for-port.sh <port> [timeout_seconds] [service_name] [service_pid]
#
# Arguments:
#   port             - TCP port to wait for (required)
#   timeout_seconds  - Max seconds to wait (default: 60)
#   service_name     - Display name for messages (default: "Service")
#   service_pid      - Optional PID; fail early if it exits before listening
#
# Exit codes:
#   0 - Port is listening
#   1 - Timed out waiting

PORT="${1:?Usage: wait-for-port.sh <port> [timeout] [service_name] [service_pid]}"
TIMEOUT="${2:-60}"
SERVICE="${3:-Service}"
SERVICE_PID="${4:-}"

case "$PORT" in
    ''|*[!0-9]*)
        echo "Port must be a numeric TCP port: $PORT" >&2
        exit 1
        ;;
esac

if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    echo "Port must be between 1 and 65535: $PORT" >&2
    exit 1
fi

elapsed=0
interval=1

is_port_listening() {
    if command -v powershell.exe >/dev/null 2>&1; then
        if WAIT_FOR_PORT_PORT="$PORT" powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\$ErrorActionPreference='SilentlyContinue'; \$Port = [int]\$env:WAIT_FOR_PORT_PORT; if (Get-NetTCPConnection -LocalPort \$Port -State Listen) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
            return 0
        fi
    fi

    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
            return 0
        fi
    fi

    if command -v ss >/dev/null 2>&1; then
        if ss -ltn "( sport = :$PORT )" 2>/dev/null | tail -n +2 | grep -q .; then
            return 0
        fi
    fi

    if command -v netstat >/dev/null 2>&1; then
        if netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[.:])${PORT}$"; then
            return 0
        fi
    fi

    return 1
}

is_service_running() {
    [ -n "$SERVICE_PID" ] || return 0
    kill -0 "$SERVICE_PID" 2>/dev/null
}

is_service_descendant() {
    local pid=$1 parent

    while [ -n "$pid" ] && [ "$pid" != "0" ]; do
        [ "$pid" = "$SERVICE_PID" ] && return 0
        parent=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]')
        [ -n "$parent" ] && [ "$parent" != "$pid" ] || break
        pid=$parent
    done

    return 1
}

is_port_owned_by_service() {
    local listener_pid

    # Without lsof we can still wait for a port, but cannot reliably connect a
    # listener to the shell wrapper PID on every supported platform.
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi

    while IFS= read -r listener_pid; do
        [ -n "$listener_pid" ] || continue
        if is_service_descendant "$listener_pid"; then
            return 0
        fi
    done < <(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)

    return 1
}

while :; do
    if ! is_service_running; then
        echo ""
        echo "✗ $SERVICE exited before listening on port $PORT"
        exit 1
    fi
    if is_port_listening; then
        if [ -n "$SERVICE_PID" ] && ! is_port_owned_by_service; then
            # A just-stopped daemon can still own the port while its replacement
            # is starting. Give that handover the normal startup window.
            if [ "$elapsed" -ge "$TIMEOUT" ]; then
                echo ""
                echo "✗ $SERVICE could not claim port $PORT; it is owned by a different process"
                exit 1
            fi
            printf "\r  Waiting for %s to claim port %s... %ds" "$SERVICE" "$PORT" "$elapsed"
            sleep "$interval"
            elapsed=$((elapsed + interval))
            continue
        fi
        break
    fi
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
        echo ""
        echo "✗ $SERVICE failed to start on port $PORT after ${TIMEOUT}s"
        exit 1
    fi
    printf "\r  Waiting for %s on port %s... %ds" "$SERVICE" "$PORT" "$elapsed"
    sleep "$interval"
    elapsed=$((elapsed + interval))
done

printf "\r  %-60s\r" ""   # clear the waiting line
