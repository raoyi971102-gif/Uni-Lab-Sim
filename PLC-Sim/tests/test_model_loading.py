from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest

from plc_sim.model_loading import ModelLoadError, load_module, load_symbol


class CatalogSelection:
    """外部目录公开合同的最小提供者；无需安装 OS。"""

    definition = SimpleNamespace(
        fqid="community.lab.pump_model",
        details={"contract": {"attachments": ("protocol",), "providers": ("analytic",)}},
    )

    def load(self):
        class Model:
            def __init__(self):
                raise AssertionError("加载目录入口不得实例化设备")

        return Model


def test_catalog_selection_loads_class_without_constructing():
    from plc_sim.model_loading import load_catalog_model

    model = load_catalog_model(CatalogSelection(), attachment="protocol", provider="analytic")
    assert isinstance(model, type)


@pytest.mark.parametrize("attachment,provider", [("direct", "analytic"), ("protocol", "physics")])
def test_catalog_rejects_unsupported_capability_before_loading(attachment, provider):
    from plc_sim.model_loading import load_catalog_model

    class UnloadableSelection(CatalogSelection):
        def load(self):
            raise AssertionError("不支持的能力不得导入模型")

    with pytest.raises(ModelLoadError, match="community.lab.pump_model.*不支持"):
        load_catalog_model(UnloadableSelection(), attachment=attachment, provider=provider)


@pytest.mark.parametrize("failure", [ImportError("missing dependency"), ValueError("digest changed"), RuntimeError("import failed")])
def test_catalog_loading_error_reports_selected_identity_and_cause(failure):
    from plc_sim.model_loading import load_catalog_model

    class FailedSelection(CatalogSelection):
        def load(self):
            raise failure

    with pytest.raises(ModelLoadError, match="community.lab.pump_model") as error:
        load_catalog_model(FailedSelection(), attachment="protocol", provider="analytic")
    assert error.value.__cause__ is failure


def test_catalog_rejects_nonclass_entry():
    from plc_sim.model_loading import load_catalog_model

    class InvalidSelection(CatalogSelection):
        def load(self):
            return lambda: None

    with pytest.raises(ModelLoadError, match="community.lab.pump_model.*类型"):
        load_catalog_model(InvalidSelection(), attachment="protocol", provider="analytic")


def test_explicit_entry_preserves_type_and_does_not_construct(monkeypatch):
    module = ModuleType("independent_device_model")

    class Model:
        def __init__(self):
            raise AssertionError("加载入口不得实例化设备")

    module.Model = Model
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert load_module(module.__name__) is module
    assert load_symbol(module.__name__ + ":Model") is Model


@pytest.mark.parametrize("reference", ["", "name", "name:", ":Model", "name:bad-name", "a:b:c"])
def test_invalid_entry_is_rejected(reference):
    with pytest.raises(ModelLoadError):
        load_symbol(reference)


def test_missing_module_and_attribute_explain_selected_entry():
    with pytest.raises(ModelLoadError, match="missing_device_model_xyz") as error:
        load_symbol("missing_device_model_xyz:Model")
    assert isinstance(error.value.__cause__, ModuleNotFoundError)
    with pytest.raises(ModelLoadError, match="没有提供入口"):
        load_symbol("json:missing_factory_xyz")
