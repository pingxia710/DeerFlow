#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
WAIT_FOR_PORT="$ROOT/scripts/wait-for-port.sh"

if ! command -v lsof >/dev/null 2>&1; then
    echo "skip: lsof is required to verify listener ownership"
    exit 0
fi

PORT="$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
HANDOVER_PORT="$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

service_pid=""
unrelated_pid=""
foreign_pid=""
handover_service_pid=""

cleanup() {
    local port listener_pids pid
    for pid in "$service_pid" "$unrelated_pid" "$foreign_pid" "$handover_service_pid"; do
        [ -z "$pid" ] || kill "$pid" 2>/dev/null || true
    done
    for pid in "$service_pid" "$unrelated_pid" "$foreign_pid" "$handover_service_pid"; do
        [ -z "$pid" ] || wait "$pid" 2>/dev/null || true
    done
    for port in "$PORT" "$HANDOVER_PORT"; do
        listener_pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
        [ -z "$listener_pids" ] || kill $listener_pids 2>/dev/null || true
    done
}

trap cleanup EXIT

sh -c "python3 -m http.server '$PORT' --bind 127.0.0.1 >/dev/null 2>&1 & wait" >/dev/null 2>&1 &
service_pid=$!

"$WAIT_FOR_PORT" "$PORT" 5 "test service" "$service_pid"

sleep 10 &
unrelated_pid=$!
if "$WAIT_FOR_PORT" "$PORT" 1 "unrelated service" "$unrelated_pid" >/dev/null 2>&1; then
    echo "expected listener ownership check to reject an unrelated PID" >&2
    exit 1
fi

python3 - "$HANDOVER_PORT" <<'PY' &
import socket
import sys
import time

with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(sys.argv[1])))
    listener.listen()
    time.sleep(0.25)
PY
foreign_pid=$!
sleep 0.05
sh -c "sleep 1; exec python3 -m http.server '$HANDOVER_PORT' --bind 127.0.0.1 >/dev/null 2>&1" &
handover_service_pid=$!
"$WAIT_FOR_PORT" "$HANDOVER_PORT" 5 "handover service" "$handover_service_pid"

echo "wait-for-port ownership checks passed"
