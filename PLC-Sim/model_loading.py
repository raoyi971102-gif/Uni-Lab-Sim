"""从显式 Python 入口加载设备包能力，不搜索历史目录或回退内置模型。"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol

from unilabos_sim_contracts import SourceLock, VerifiedSource, verify_source


class ModelLoadError(ImportError):
    """选定设备包能力无法加载。"""


class SimulationDefinition(Protocol):
    """外部目录的只读模型声明，不依赖 OS 的实现包。"""

    @property
    def fqid(self) -> str: ...

    @property
    def details(self) -> Mapping[str, Any]: ...


class SimulationSelection(Protocol):
    """由目录负责来源校验与延迟导入的已选模型。"""

    @property
    def definition(self) -> SimulationDefinition: ...

    def load(self) -> type: ...


def load_catalog_model(selected: SimulationSelection, *, attachment: str, provider: str) -> type:
    """消费已选目录入口；不扫描目录、不构造设备或启动仿真。"""
    contract = selected.definition.details["contract"]
    if attachment not in contract["attachments"] or provider not in contract["providers"]:
        raise ModelLoadError(
            f"模型 {selected.definition.fqid} 不支持 attachment={attachment!r}, provider={provider!r}"
        )
    try:
        model = selected.load()
    except Exception as exc:
        raise ModelLoadError(f"无法加载目录模型 {selected.definition.fqid}: {exc}") from exc
    if not isinstance(model, type):
        raise ModelLoadError(f"目录模型 {selected.definition.fqid} 入口必须返回类型")
    return model


@dataclass(frozen=True)
class ModelSource:
    """调用方冻结的锁及独立来源采集结果；不自行猜测 Git 或 wheel 身份。"""

    lock: SourceLock
    root: Path
    actual_commit: str
    actual_dirty_digest: str | None

    def verify(self, imported_file: str) -> VerifiedSource:
        return verify_source(self.lock, root=self.root, imported_file=Path(imported_file),
                             actual_commit=self.actual_commit, actual_dirty_digest=self.actual_dirty_digest)


def load_module(name: str, *, source: ModelSource | None = None) -> ModuleType:
    """仅加载调用方选择的模块；不会实例化设备或启动仿真。"""
    if not isinstance(name, str) or not name or any(not part.isidentifier() for part in name.split(".")):
        raise ModelLoadError(f"设备模型模块名无效: {name!r}")
    try:
        if source is not None:
            # 先校验将被导入的文件，再校验实际模块，拒绝旧 editable 及缓存串源。
            spec = importlib.util.find_spec(name)
            if spec is None or not spec.origin:
                raise ValueError("选定模型没有可核验的来源文件")
            source.verify(spec.origin)
        module = importlib.import_module(name)
        if source is not None:
            location = getattr(module, "__file__", None)
            if not location:
                raise ValueError("实际模型模块没有来源文件")
            source.verify(location)
        return module
    except (ImportError, ValueError, OSError) as exc:
        if source is None and not isinstance(exc, ImportError):
            raise
        raise ModelLoadError(
            f"无法加载设备包模块 {name}；请安装所选版本的驱动包及其依赖。原始错误: {exc}"
        ) from exc


def load_symbol(reference: str, *, source: ModelSource | None = None) -> Any:
    """解析 module:attribute 入口，保留原始类型身份。"""
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ModelLoadError(f"设备模型入口须为 module:attribute: {reference!r}")
    module, attribute = reference.split(":")
    if not attribute or any(not part.isidentifier() for part in attribute.split(".")):
        raise ModelLoadError(f"设备模型属性名无效: {reference!r}")
    value: Any = load_module(module, source=source)
    try:
        for part in attribute.split("."):
            value = getattr(value, part)
    except AttributeError as exc:
        raise ModelLoadError(f"设备包没有提供入口: {reference}") from exc
    return value
