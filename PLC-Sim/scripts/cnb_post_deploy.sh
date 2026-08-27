#!/usr/bin/env bash
# CNB rsync 之后在国内机执行：装依赖、重启 systemd GUI、健康检查。
# 逻辑放在仓库脚本里，避免 tencentcom/rsync 的 script 引号嵌套把多行命令弄坏。
set -euo pipefail

ROOT=/www/wwwroot/PLC-Sim
cd "$ROOT"

bash scripts/ensure_deploy_venv.sh "$ROOT"
mkdir -p data/uploads data/runtime
chown -R www:www "$ROOT"

# GUI 托管模式下 Server/Agent 是子进程，重启前清掉，避免占 4855
pkill -f "$ROOT/server\.py" || true
pkill -f "$ROOT/[a-z_]*handshake_agent\.py" || true

OLD_PID="$(systemctl show -p MainPID --value plcsim-gui || true)"
systemctl restart plcsim-gui

for _ in $(seq 30); do
  sleep 1
  NEW_PID="$(systemctl show -p MainPID --value plcsim-gui || true)"
  [ -n "$NEW_PID" ] && [ "$NEW_PID" != "0" ] && [ "$NEW_PID" != "$OLD_PID" ] || continue
  if curl -fsS http://127.0.0.1:18765/api/health >/dev/null 2>&1; then
    echo "GUI 已恢复 (pid $OLD_PID -> $NEW_PID)"
    exit 0
  fi
done

echo "GUI 未在 30 秒内恢复" >&2
systemctl status plcsim-gui --no-pager || true
exit 1
