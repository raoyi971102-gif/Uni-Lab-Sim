#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

SERVER_PID=""
AGENT_PID=""

plcsim_stop_child() {
    local pid="$1"

    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
        wait "$pid" >/dev/null 2>&1 || true
    fi
}

plcsim_all_exit() {
    local status=$?
    trap - EXIT INT TERM
    plcsim_stop_child "$AGENT_PID"
    plcsim_stop_child "$SERVER_PID"
    plcsim_report_exit "$status"
    exit "$status"
}
trap plcsim_all_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

plcsim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

LISTEN_HOST="${PLCSIM_HOST:-0.0.0.0}"
CLIENT_HOST="${PLCSIM_CLIENT_HOST:-127.0.0.1}"
PORT="${PLCSIM_PORT:-4855}"

case "$PORT" in
    ''|*[!0-9]*)
        plcsim_log "[X] PLCSIM_PORT 必须是数字，当前值: $PORT"
        exit 2
        ;;
esac

CSV_ARGS=()
for arg in "$@"; do
    case "$arg" in
        *.[cC][sS][vV]) CSV_ARGS+=(--csv "$arg") ;;
        *)
            plcsim_log "[X] start_all.command 只接受 CSV 文件参数: $arg"
            plcsim_log "    其他 CLI 参数请使用 start.command 和 start_szlab_handshake.command。"
            exit 2
            ;;
    esac
done

ENDPOINT="opc.tcp://$CLIENT_HOST:$PORT/xuse_sim/"

printf '\n==============================================================\n'
printf '  PLC-Sim Server + SZLab Handshake Agent\n'
printf '  Endpoint: %s\n' "$ENDPOINT"
printf '  按 Ctrl+C 同时停止两个进程。\n'
printf '==============================================================\n\n'

"$PLCSIM_PY" "$PROJECT_ROOT/server.py" \
    --host "$LISTEN_HOST" --port "$PORT" "${CSV_ARGS[@]}" &
SERVER_PID=$!

plcsim_log "正在等待 OPC UA Server 就绪..."
if ! plcsim_wait_for_tcp "$CLIENT_HOST" "$PORT" 150; then
    plcsim_log "[X] 15 秒内未检测到 Server 端口。"
    exit 1
fi
if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    plcsim_log "[X] OPC UA Server 已提前退出。"
    exit 1
fi

plcsim_log "Server 已就绪，启动 SZLab Handshake Agent..."
"$PLCSIM_PY" "$PROJECT_ROOT/szlab_handshake_agent.py" \
    --url "$ENDPOINT" \
    --config "$PROJECT_ROOT/config/szlab_handshake.yaml" &
AGENT_PID=$!

while kill -0 "$SERVER_PID" >/dev/null 2>&1 \
    && kill -0 "$AGENT_PID" >/dev/null 2>&1; do
    sleep 1
done

if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    if wait "$SERVER_PID"; then
        exit 0
    else
        exit $?
    fi
fi

if wait "$AGENT_PID"; then
    exit 0
else
    exit $?
fi
