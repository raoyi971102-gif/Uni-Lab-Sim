"""Managed virtual serial pairs for local Modbus integration testing."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import select
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import ConfigError

COM0COM_PROJECT_URL = "https://sourceforge.net/projects/com0com/"
COM0COM_VERSION = "3.0.0.0"
COM0COM_INSTALLER_NAME = "Setup_com0com_v3.0.0.0_W7_x64_signed.exe"
COM0COM_INSTALLER_SHA256 = (
    "26486b28604b49a9008c54feb11b9ece0008a8287ee5caf0bcf2a62f4317128f"
)
_COM_PORT_PATTERN = re.compile(r"COM([1-9][0-9]{0,2})", re.IGNORECASE)
_PAIR_ID_PATTERN = re.compile(r"CNC[AB]([0-9]+)", re.IGNORECASE)


@dataclass(frozen=True)
class VirtualSerialPair:
    simulator_port: str
    client_port: str
    backend: str
    pair_id: str | None = None
    persistent: bool = False


class VirtualSerialManager:
    """Own at most one virtual null-modem pair created by this app process."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        setupc_path: str | Path | None = None,
        installer_path: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        elevated_runner: Callable[[Path, Sequence[str]], int] | None = None,
        admin_check: Callable[[], bool] | None = None,
        setupc_finder: Callable[[str], Path | None] | None = None,
        installer_sha256: str = COM0COM_INSTALLER_SHA256,
        driver_wait_seconds: float = 10.0,
    ) -> None:
        self._platform = platform_name or sys.platform
        self._runner = runner
        self._setupc_finder = setupc_finder or _find_setupc
        self._setupc = (
            Path(setupc_path)
            if setupc_path is not None
            else self._setupc_finder(self._platform)
        )
        self._installer = (
            Path(installer_path)
            if installer_path is not None
            else _find_bundled_installer(self._platform)
        )
        self._installer_sha256 = installer_sha256.lower()
        self._admin_check = admin_check or _is_windows_administrator
        self._elevated_runner = elevated_runner or _run_windows_elevated
        self._driver_wait_seconds = max(0.0, driver_wait_seconds)
        self._pair: VirtualSerialPair | None = None
        self._bridge: _PtyBridge | None = None
        self._lock = threading.Lock()

    @property
    def active_pair(self) -> VirtualSerialPair | None:
        return self._pair

    def status(self) -> dict[str, Any]:
        if self._platform.startswith("win"):
            if self._setupc is None:
                self._setupc = self._setupc_finder(self._platform)
            driver_installed = self._setupc is not None
            installer_available = (
                self._installer is not None and self._installer.is_file()
            )
            administrator = self._admin_check()
            if driver_installed and administrator:
                message = "com0com 已就绪；可直接创建或移除虚拟 COM 串口对。"
            elif driver_installed:
                message = "com0com 已就绪；创建或移除端口对时 Windows 将请求 UAC 授权。"
            elif installer_available:
                message = "未检测到 com0com；可安装随安装包提供的官方签名驱动，Windows 将请求 UAC 授权。"
            else:
                message = "未检测到 com0com，且当前版本不含内置驱动安装程序；请从官方项目安装。"
            backend = "com0com"
            supported = driver_installed
            can_create = driver_installed
            can_install_driver = not driver_installed and installer_available
        elif os.name == "posix" or self._platform.startswith(("linux", "darwin")):
            driver_installed = True
            installer_available = False
            administrator = None
            message = "使用系统 PTY 创建临时串口对；关闭 Modbus-Sim 后自动释放。"
            backend = "pty"
            supported = True
            can_create = True
            can_install_driver = False
        else:
            driver_installed = False
            installer_available = False
            administrator = None
            message = f"当前平台 {self._platform} 暂不支持虚拟串口。"
            backend = "unsupported"
            supported = False
            can_create = False
            can_install_driver = False
        return {
            "platform": self._platform,
            "backend": backend,
            "supported": supported,
            "driver_installed": driver_installed,
            "installer_available": installer_available,
            "administrator": administrator,
            "can_create": can_create,
            "can_install_driver": can_install_driver,
            "active_pair": asdict(self._pair) if self._pair else None,
            "driver_url": COM0COM_PROJECT_URL
            if self._platform.startswith("win")
            else None,
            "message": message,
            "electrical_layer_emulated": False,
        }

    def install_driver(self) -> None:
        """Install the bundled com0com driver, elevating only this operation."""
        if not self._platform.startswith("win"):
            raise ConfigError("仅 Windows 支持安装 com0com 虚拟串口驱动")
        with self._lock:
            if self._setupc is not None:
                return
            if self._installer is None or not self._installer.is_file():
                raise ConfigError("当前 Modbus-Sim 安装不含 com0com 驱动安装程序")
            actual_sha256 = _sha256(self._installer)
            if actual_sha256 != self._installer_sha256:
                raise ConfigError("内置 com0com 安装程序校验失败；为安全起见已阻止安装")
            self._run_privileged(self._installer, "/S")

            deadline = time.monotonic() + self._driver_wait_seconds
            while True:
                self._setupc = self._setupc_finder(self._platform)
                if self._setupc is not None:
                    return
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
            raise ConfigError(
                "com0com 安装程序已结束，但未找到 setupc.exe；请重启 Modbus-Sim 后检查驱动状态"
            )

    def create(
        self,
        port_a: str = "COM10",
        port_b: str = "COM11",
        *,
        occupied_ports: Sequence[str] = (),
    ) -> VirtualSerialPair:
        with self._lock:
            if self._pair is not None:
                raise ConfigError("虚拟串口对已经存在；请先移除当前串口对")
            if self._platform.startswith("win"):
                self._pair = self._create_windows_pair(port_a, port_b, occupied_ports)
            elif os.name == "posix" or self._platform.startswith(("linux", "darwin")):
                self._bridge = _PtyBridge.create()
                self._pair = VirtualSerialPair(
                    simulator_port=self._bridge.port_a,
                    client_port=self._bridge.port_b,
                    backend="pty",
                )
            else:
                raise ConfigError(f"当前平台 {self._platform} 暂不支持虚拟串口")
            return self._pair

    def remove(self) -> None:
        with self._lock:
            if self._pair is None:
                return
            if self._pair.backend == "pty":
                assert self._bridge is not None
                self._bridge.close()
                self._bridge = None
                self._pair = None
                return
            if self._pair.backend == "com0com":
                if self._pair.pair_id is None:
                    raise ConfigError(
                        "无法确定 com0com 串口对编号；请使用 com0com Setup 移除该串口对"
                    )
                self._run_setupc("remove", self._pair.pair_id, elevated=True)
                self._pair = None

    def close(self) -> None:
        """Release process-owned PTYs; persistent Windows driver pairs stay installed."""
        with self._lock:
            if self._bridge is not None:
                self._bridge.close()
                self._bridge = None
                self._pair = None

    def _create_windows_pair(
        self,
        port_a: str,
        port_b: str,
        occupied_ports: Sequence[str],
    ) -> VirtualSerialPair:
        if self._setupc is None:
            raise ConfigError("未检测到 com0com；请先从官方项目安装虚拟串口驱动")
        normalized_a = _normalize_com_port(port_a)
        normalized_b = _normalize_com_port(port_b)
        if normalized_a == normalized_b:
            raise ConfigError("两个虚拟 COM 端口名称不能相同")
        occupied = {item.upper() for item in occupied_ports}
        conflicts = [item for item in (normalized_a, normalized_b) if item in occupied]
        if conflicts:
            raise ConfigError(f"COM 端口已被占用: {', '.join(conflicts)}")
        before = self._list_windows_pair_ids()
        result = self._run_setupc(
            "install",
            f"PortName={normalized_a}",
            f"PortName={normalized_b}",
            elevated=True,
        )
        try:
            after = self._list_windows_pair_ids()
            candidates = after - before
        except ConfigError:
            # Installation already changed system state; keep the usable pair even
            # when a follow-up inventory command is unavailable.
            candidates = set()
        if not candidates:
            candidates = set(
                _PAIR_ID_PATTERN.findall(f"{result.stdout}\n{result.stderr}")
            )
        pair_id = sorted(candidates, key=int)[-1] if candidates else None
        return VirtualSerialPair(
            simulator_port=normalized_a,
            client_port=normalized_b,
            backend="com0com",
            pair_id=pair_id,
            persistent=True,
        )

    def _list_windows_pair_ids(self) -> set[str]:
        result = self._run_setupc("list")
        return set(_PAIR_ID_PATTERN.findall(f"{result.stdout}\n{result.stderr}"))

    def _run_setupc(
        self,
        *arguments: str,
        elevated: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if self._setupc is None:
            raise ConfigError("未检测到 com0com setupc.exe")
        if elevated and not self._admin_check():
            self._run_privileged(self._setupc, *arguments)
            return subprocess.CompletedProcess(
                [str(self._setupc), *arguments],
                0,
                stdout="",
                stderr="",
            )
        try:
            result = self._runner(
                [str(self._setupc), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigError(f"执行 com0com 失败: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知错误").strip()
            raise ConfigError(f"com0com 命令失败: {detail}")
        return result

    def _run_privileged(self, executable: Path, *arguments: str) -> None:
        try:
            return_code = self._elevated_runner(executable, arguments)
        except ConfigError:
            raise
        except OSError as exc:
            raise ConfigError(f"启动管理员操作失败: {exc}") from exc
        if return_code != 0:
            raise ConfigError(f"管理员操作失败，退出码 {return_code}")


class _PtyBridge:
    def __init__(
        self, master_a: int, slave_a: int, master_b: int, slave_b: int
    ) -> None:
        self._fds = (master_a, slave_a, master_b, slave_b)
        self._masters = (master_a, master_b)
        self.port_a = os.ttyname(slave_a)
        self.port_b = os.ttyname(slave_b)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._forward, name="modbus-sim-pty", daemon=True
        )
        self._thread.start()

    @classmethod
    def create(cls) -> "_PtyBridge":
        try:
            import pty
            import tty

            master_a, slave_a = pty.openpty()
            master_b, slave_b = pty.openpty()
            tty.setraw(slave_a)
            tty.setraw(slave_b)
            os.set_blocking(master_a, False)
            os.set_blocking(master_b, False)
            return cls(master_a, slave_a, master_b, slave_b)
        except (ImportError, OSError) as exc:
            for descriptor in (
                locals().get("master_a", None),
                locals().get("slave_a", None),
                locals().get("master_b", None),
                locals().get("slave_b", None),
            ):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise ConfigError(f"创建 PTY 虚拟串口失败: {exc}") from exc

    def _forward(self) -> None:
        routes = {
            self._masters[0]: self._masters[1],
            self._masters[1]: self._masters[0],
        }
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select(self._masters, (), (), 0.1)
            except (OSError, ValueError):
                break
            for source in readable:
                try:
                    data = os.read(source, 4096)
                    if data:
                        _write_all(routes[source], data)
                except OSError as exc:
                    if exc.errno not in {errno.EAGAIN, errno.EIO, errno.EBADF}:
                        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        for descriptor in self._fds:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(descriptor, view)
            view = view[written:]
        except BlockingIOError:
            select.select((), (descriptor,), (), 0.1)


def _normalize_com_port(value: str) -> str:
    normalized = value.strip().upper()
    match = _COM_PORT_PATTERN.fullmatch(normalized)
    if match is None or int(match.group(1)) > 256:
        raise ConfigError("虚拟端口名称必须是 COM1..COM256")
    return normalized


def _find_setupc(platform_name: str) -> Path | None:
    if not platform_name.startswith("win"):
        return None
    configured = os.environ.get("MODBUSSIM_COM0COM_SETUPC")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(
                (
                    Path(root) / "com0com" / "setupc.exe",
                    Path(root) / "com0com" / "setupc" / "setupc.exe",
                )
            )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _find_bundled_installer(platform_name: str) -> Path | None:
    if not platform_name.startswith("win"):
        return None
    configured = os.environ.get("MODBUSSIM_COM0COM_INSTALLER")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if getattr(sys, "frozen", False):
        candidates.append(
            Path(sys.executable).resolve().parent
            / "third_party"
            / "com0com"
            / COM0COM_INSTALLER_NAME
        )
    candidates.append(
        Path(__file__).resolve().parent
        / "third_party"
        / "com0com"
        / COM0COM_INSTALLER_NAME
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_windows_elevated(executable: Path, arguments: Sequence[str]) -> int:
    """Run one executable via the Windows ``runas`` verb and wait for it."""
    if not sys.platform.startswith("win"):
        raise OSError("Windows UAC is unavailable on this platform")

    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    execute = shell32.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    wait = kernel32.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = str(executable)
    info.lpParameters = subprocess.list2cmdline(list(arguments))
    info.nShow = 0
    if not execute(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise ConfigError("用户取消了 Windows UAC 授权")
        raise OSError(error, "ShellExecuteExW failed")
    if not info.hProcess:
        raise OSError("Windows 未返回管理员进程句柄")
    try:
        if wait(info.hProcess, 0xFFFFFFFF) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "WaitForSingleObject failed")
        exit_code = wintypes.DWORD()
        if not get_exit_code(info.hProcess, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return int(exit_code.value)
    finally:
        close_handle(info.hProcess)


def _is_windows_administrator() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
