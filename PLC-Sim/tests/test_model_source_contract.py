"""正式模型加载入口在返回构造器前核验来源；不授予设备能力。"""

import hashlib
import importlib
from pathlib import Path
import sys

import pytest

from plc_sim.model_loading import ModelLoadError, ModelSource, load_symbol
from unilabos_sim_contracts import SourceFile, SourceLock


def requirement(root: Path, files: tuple[Path, ...]) -> ModelSource:
    lock = SourceLock(source_id="selected-model", commit="a" * 40, dirty_digest="b" * 64,
        files=tuple(SourceFile(path=file.relative_to(root).as_posix(),
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest()) for file in files))
    return ModelSource(lock, root, lock.commit, lock.dirty_digest)


def test_real_installed_public_type_preserved() -> None:
    from plc_sim import cosimulation
    file = Path(cosimulation.__file__)
    source = requirement(file.parent, (file,))
    selected = load_symbol("plc_sim.cosimulation:Channel", source=source)
    assert selected is cosimulation.Channel
    assert selected(period_ticks=2).period_ticks == 2


@pytest.mark.parametrize("failure", ["old_path", "missing_resource", "changed_file", "commit", "cached_module"])
def test_source_refusal_precedes_module_execution(tmp_path: Path, monkeypatch, failure: str) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    module = selected / "isolated_contract_model.py"
    module.write_text("raise AssertionError('模型顶层不应执行')\n", encoding="utf-8")
    resource = selected / "required.json"
    resource.write_text("{}", encoding="utf-8")
    source = requirement(selected, (module, resource))
    monkeypatch.syspath_prepend(str(selected))
    if failure == "old_path":
        old = tmp_path / "old"
        old.mkdir()
        (old / module.name).write_bytes(module.read_bytes())
        monkeypatch.syspath_prepend(str(old))
    elif failure == "missing_resource":
        resource.unlink()
    elif failure == "changed_file":
        module.write_text("raise AssertionError('篡改模型不应执行')\n", encoding="utf-8")
    elif failure == "commit":
        source = ModelSource(source.lock, source.root, "c" * 40, source.actual_dirty_digest)
    else:
        from types import ModuleType
        cached = ModuleType("isolated_contract_model")
        cached.__spec__ = importlib.util.spec_from_file_location(cached.__name__, module)
        cached.__file__ = str(tmp_path / "old.py")
        monkeypatch.setitem(sys.modules, cached.__name__, cached)
    importlib.invalidate_caches()
    with pytest.raises(ModelLoadError):
        load_symbol("isolated_contract_model:Model", source=source)


def test_legacy_load_preserves_module_value_error(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "legacy_value_error.py").write_text("raise ValueError('原初始化错误')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ValueError, match="原初始化错误"):
        load_symbol("legacy_value_error:Model")
