#!/bin/bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
. "$PROJECT_ROOT/scripts/unix_common.sh"

plcsim_gui_exit() {
    local status=$?
    trap - EXIT
    plcsim_report_exit "$status"
    exit "$status"
}
trap plcsim_gui_exit EXIT

plcsim_ensure_venv "$PROJECT_ROOT"
cd "$PROJECT_ROOT"

printf '\n==============================================================\n'
printf '  PLC-Sim Web GUI\n'
printf '  默认 URL: http://127.0.0.1:18765/\n'
printf '  浏览器将自动打开；按 Ctrl+C 停止。\n'
printf '==============================================================\n\n'

"$PLCSIM_PY" -m gui.backend "$@"
