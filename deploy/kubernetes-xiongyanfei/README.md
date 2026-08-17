# PLC-Sim GUI 管理模式

本清单只面向远端 Kubernetes 的 `xiongyanfei` 命名空间。PLC-Sim 以 Web GUI
进程常驻运行，OPC UA Server 与 SZLab Handshake Agent 都由 GUI 启停，不作为
独立容器启动。

## 固定源码构建

```bash
set -euo pipefail

BUILD_ROOT="$(mktemp -d /home/xiongyanfei/.plc-sim-build.XXXXXX)"
PLC_SOURCE="$BUILD_ROOT/PLC-Sim"

cleanup_build_worktree() {
  git worktree remove --force "$PLC_SOURCE" >/dev/null 2>&1 || true
  rmdir "$BUILD_ROOT" >/dev/null 2>&1 || true
}
trap cleanup_build_worktree EXIT

git worktree add --detach "$PLC_SOURCE" \
  7c3c36e98cc35055157f6478ff43fd5307edec85
test "$(git -C "$PLC_SOURCE" rev-parse HEAD)" = \
  7c3c36e98cc35055157f6478ff43fd5307edec85
test -z "$(git -C "$PLC_SOURCE" status --porcelain)"

nerdctl -n k8s.io build \
  --label io.unilab.plc-sim.commit=7c3c36e98cc35055157f6478ff43fd5307edec85 \
  -t plc-sim:7c3c36e-gui-v1 \
  "$PLC_SOURCE"
```

## 部署和访问

```bash
kubectl create namespace xiongyanfei --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/kubernetes-xiongyanfei/plc-sim.yaml
kubectl rollout status deployment/plc-sim -n xiongyanfei --timeout=5m
```

- Web GUI：`http://115.190.137.109:30160`
- OPC UA：`opc.tcp://115.190.137.109:30161/xuse_sim/`
- 集群内 Uni-Lab-OS 使用：`opc.tcp://plc-sim:4855`

GUI 和 OPC UA 端口均直接暴露公网，仅用于明确接受无 TLS/认证风险的临时调试
环境。首次部署后需要在 GUI 中启动 OPC UA Server；握手仿真还需启动 SZLab
Handshake Agent。联合部署时必须先启动这两个 GUI 子进程，再部署等待
`plc-sim:4855` 的 Uni-Lab-OS；完整顺序见 Uni-Lab-OS 的
`deploy/kubernetes-xiongyanfei/szlab-local-debug/README.md`。
