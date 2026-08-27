#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

plcsim_szlab_exit() {
    local status=$?
    trap - EXIT
    plcsim_report_exit "$status"
    exit "$status"
}
trap plcsim_szlab_exit EXIT

plcsim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

printf '\n==============================================================\n'
printf '  SZLab Poly Studio Handshake Simulator\n'
printf '  Target: opc.tcp://127.0.0.1:4855/xuse_sim/\n'
printf '  按 Ctrl+C 停止。\n'
printf '==============================================================\n\n'

"$PLCSIM_PY" "$PROJECT_ROOT/szlab_handshake_agent.py" "$@"
