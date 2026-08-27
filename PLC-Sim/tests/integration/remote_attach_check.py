"""GUI 远程部署相关能力的自检。

拉起一个独立的 server.py（模拟 Supervisor 托管），再拉起带 --attach-url 的 GUI，
然后断言：状态如实上报为 attached、变量能读、能按类型写并回读、
GUI 侧的启停接口被正确拒绝，以及变量表能通过上传接口落盘。

    python tests/integration/remote_attach_check.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "demo_variables.csv"
OPC_PORT = 4877
GUI = "http://127.0.0.1:18799"
OPC_URL = f"opc.tcp://127.0.0.1:{OPC_PORT}/xuse_sim/"


def call(path: str, payload=None, timeout=10):
    """返回 (状态码, 解析后的 JSON)；HTTP 错误不抛异常，交给调用方断言。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        GUI + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def wait_ready(deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            if call("/api/health", timeout=2)[0] == 200:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise TimeoutError("GUI 未在预期时间内就绪")


def main() -> int:
    connection_state = (
        Path(tempfile.gettempdir())
        / f"plcsim-connections-{os.getpid()}.json"
    )
    child_env = os.environ.copy()
    child_env["PLCSIM_CONNECTION_STATE"] = str(connection_state)
    procs = [
        subprocess.Popen([sys.executable, str(ROOT / "server.py"),
                          "--host", "127.0.0.1", "--port", str(OPC_PORT),
                          "--csv", str(CSV)],
                         cwd=ROOT, env=child_env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    time.sleep(4)
    procs.append(
        subprocess.Popen([sys.executable, "-m", "gui.backend",
                          "--host", "127.0.0.1", "--port", "18799", "--no-open",
                          "--attach-url", OPC_URL, "--attach-csv", str(CSV)],
                         cwd=ROOT, env=child_env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    )
    try:
        wait_ready(time.monotonic() + 30)

        _, state = call("/api/state")
        srv = state["server"]
        assert srv["attached"] is True, f"未进入挂接模式: {srv}"
        assert srv["running"] is True, f"挂接后应上报运行中: {srv}"
        assert srv["endpoint"] == OPC_URL, f"endpoint 不符: {srv}"
        assert srv["variable_count"] == 16, f"变量数应为 16: {srv}"
        assert srv["connections"]["available"] is True, f"连接遥测不可用: {srv}"
        assert state["agent"]["attached"] is True, "Agent 也应标记为外部托管"
        print(f"[test] 挂接状态 OK：{srv['variable_count']} 个变量 @ {srv['endpoint']}")

        code, page = call("/api/server/variables?limit=5")
        assert code == 200 and len(page["items"]) == 5, f"变量分页读取失败: {code} {page}"
        print(f"[test] 在线读取 OK：{page['items'][0]['name']} = {page['items'][0]['value']}")

        monitored_ids = [item["node_id"] for item in page["items"][:2]]
        code, monitored = call(
            "/api/server/variables/read",
            {"node_ids": monitored_ids + ["ns=4;s=missing|node"]},
        )
        assert code == 200 and [item["node_id"] for item in monitored["items"]] == monitored_ids, \
            f"监控栏批量读取顺序不符: {code} {monitored}"
        assert monitored["missing"] == ["ns=4;s=missing|node"], \
            f"失效监控变量未被报告: {monitored}"
        print("[test] 监控栏批量读取 OK")

        # 写一个 INT16：证明走的是外部 Server 而不是本进程的假状态
        target = next(i for i in call("/api/server/variables?limit=100")[1]["items"]
                      if i["data_type"] == "INT16")
        code, wrote = call("/api/server/variable",
                           {"node_id": target["node_id"], "value": 1234})
        assert code == 200 and wrote["value"] == 1234, f"写入未生效: {code} {wrote}"
        readback = next(i for i in call("/api/server/variables?limit=100")[1]["items"]
                        if i["node_id"] == target["node_id"])
        assert readback["value"] == 1234, f"回读不一致: {readback}"
        print(f"[test] 在线写入 OK：{target['name']} = {readback['value']}")

        # 挂接模式下 GUI 不得再自己启停，否则会和 Supervisor 抢 4855 端口
        for path in ("/api/server/start", "/api/server/stop",
                     "/api/agent/start", "/api/agent/stop"):
            code, _ = call(path, {})
            assert code == 400, f"{path} 应被拒绝，实际 {code}"
        print("[test] 启停接口已正确拒绝（避免与进程管理器抢端口）")

        # 上传变量表：浏览器所在机器和服务器不是同一台时的唯一入口
        payload = base64.b64encode(CSV.read_bytes()).decode()
        code, up = call("/api/csv/upload",
                        {"filename": r"C:\Users\x\uploaded.csv", "content_b64": payload})
        assert code == 200 and up["count"] == 16, f"上传失败: {code} {up}"
        saved = Path(up["path"])
        assert saved.parent == ROOT / "data" / "uploads", f"落盘位置不对: {saved}"
        assert saved.read_bytes() == CSV.read_bytes(), "落盘字节与源文件不一致"
        print(f"[test] 上传 OK：{up['count']} 个节点 → {saved}")

        # 目录穿越必须被剥掉，而不是写到 data/uploads 外面
        code, esc = call("/api/csv/upload",
                         {"filename": "../../../tmp/escaped.csv", "content_b64": payload})
        assert code == 200 and Path(esc["path"]).parent == ROOT / "data" / "uploads", \
            f"目录穿越未被拦住: {esc}"
        Path(esc["path"]).unlink(missing_ok=True)
        saved.unlink(missing_ok=True)
        print("[test] 目录穿越已被剥离")

        for bad, why in (
            ({"filename": "x.txt", "content_b64": payload}, "非 .csv"),
            ({"filename": "x.csv", "content_b64": "!!!not-base64!!!"}, "非法 base64"),
            ({"filename": "x.csv", "content_b64": base64.b64encode(b"a,b\n1,2\n").decode()},
             "无 VARIABLE 节点"),
        ):
            code, _ = call("/api/csv/upload", bad)
            assert code == 400, f"{why} 应被拒绝，实际 {code}"
        print("[test] 非法上传已正确拒绝")

        print("[test] PASS：全部通过")
        return 0
    except AssertionError as exc:
        print(f"[test] FAIL：{exc}")
        return 1
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        connection_state.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
