"""在私有共享目录中连接不同Python进程的物理端口。

一个调用方和一个世界工作进程；不是网络服务，不接受现场端点。请求开始后若
进程崩溃，不重放该请求。超时交由CoupledSession冻结，只有显式新epoch复位可恢复。
"""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import math
import os
import time
from uuid import uuid4
from .cosimulation import CouplingError, PhysicsFrame, Sample, StepToken

LIMIT = 2 * 1024 * 1024


def _write(path: Path, value: dict) -> None:
    raw = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > LIMIT:
        raise CouplingError("物理消息超过大小限制")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _read(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > LIMIT:
        raise CouplingError("物理消息路径或大小无效")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise CouplingError("物理消息须为对象")
    return value


def _directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("邮箱必须为调用方准备的私有普通目录")
    return path


class FilePhysicsPort:
    """只在调用线程等待确认；不因为超时重发可能已经执行的物理步。"""
    def __init__(self, directory: Path, *, timeout_s: float = 30.):
        self.directory = _directory(directory)
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("等待时间须为有限正数")
        self.timeout_s = float(timeout_s)

    def _call(self, operation, token, commands, channels):
        identity = str(time.time_ns()).zfill(24) + "-" + uuid4().hex
        request = {"schema": "lab.physics-mailbox/v1", "request_id": identity,
                   "operation": operation, "token": asdict(token),
                   "commands": commands, "channels": list(channels)}
        _write(self.directory / (identity + ".request.json"), request)
        response_path = self.directory / (identity + ".response.json")
        deadline = time.monotonic() + self.timeout_s
        while not response_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("物理步确认超时，执行结果未知，禁止重放")
            time.sleep(.002)
        response = _read(response_path)
        if response.get("request_id") != identity or response.get("schema") != request["schema"]:
            raise CouplingError("物理响应身份不符")
        if response.get("error"):
            raise CouplingError("物理工作进程拒绝请求：" + str(response["error"]))
        frame = response["frame"]
        return PhysicsFrame(StepToken(**frame["token"]), frame["elapsed_ns"],
                            {name: Sample(**sample) for name, sample in frame["samples"].items()})

    def step(self, token, commands, sample_channels):
        return self._call("step", token, dict(commands), sample_channels)

    def reset(self, token, sample_channels):
        return self._call("reset", token, {}, sample_channels)


class PhysicsMailboxWorker:
    """工作进程在Isaac世界所有者线程调用serve_once，不创建额外SDK线程。"""
    def __init__(self, directory: Path, backend):
        self.directory = _directory(directory)
        self.backend = backend
        self._handled = set()

    def serve_once(self) -> bool:
        for path in sorted(self.directory.glob("*.request.json")):
            identity = path.name.removesuffix(".request.json")
            if identity in self._handled:
                continue
            response_path = self.directory / (identity + ".response.json")
            if response_path.exists():
                self._handled.add(identity)
                continue
            response = {"schema": "lab.physics-mailbox/v1", "request_id": identity}
            started = self.directory / (identity + ".started")
            try:
                request = _read(path)
                if (set(request) != {"schema", "request_id", "operation", "token", "commands", "channels"}
                        or request["schema"] != response["schema"] or request["request_id"] != identity
                        or request["operation"] not in {"step", "reset"}
                        or not isinstance(request["commands"], dict)
                        or not isinstance(request["channels"], list)
                        or any(not isinstance(name, str) or not name for name in request["channels"])
                        or len(set(request["channels"])) != len(request["channels"])):
                    raise CouplingError("请求格式非法")
                token = StepToken(**request["token"])
                if request["operation"] == "reset" and request["commands"]:
                    raise CouplingError("复位不能夹带执行命令")
                # 已开始但无响应的遗留请求，其副作用不明；重启后绝不重放。
                with started.open("xb"):
                    pass
                if request["operation"] == "reset":
                    frame = self.backend.reset(token, tuple(request["channels"]))
                else:
                    frame = self.backend.step(token, request["commands"], tuple(request["channels"]))
                response["frame"] = asdict(frame)
            except FileExistsError:
                response["error"] = "此前请求已开始但缺少确认，必须显式复位"
            except Exception as error:
                response["error"] = type(error).__name__ + ":" + str(error)
            _write(response_path, response)
            self._handled.add(identity)
            return True
        return False
