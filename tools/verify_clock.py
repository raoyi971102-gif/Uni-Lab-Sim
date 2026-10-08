"""已安装 wheel 的时钟回归；核对来源，不启动 GPU 或协议服务。"""
from __future__ import annotations
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-source', type=Path, required=True)
    parser.add_argument('--core-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    plc = repo / 'PLC-Sim'
    core = args.core_source.resolve(strict=True)
    commit = subprocess.check_output(['git', '-C', str(core), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != args.core_commit:
        raise ValueError('合同来源提交不匹配')
    if args.output.exists():
        raise ValueError('不得覆盖既有验收回执')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    inputs = {str(p.resolve()): digest(p) for root in (plc, core) for p in root.rglob('*.py')}
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    sys.dont_write_bytecode = True
    # 仅测试文件互相引用；产品必须来自已安装 wheel，不能注入源码包路径。
    sys.path.insert(0, str(plc/'tests'))
    for name in ('plc_sim.cosimulation','plc_sim.source_barrier','plc_sim.session_host','plc_sim.process_dynamics','unilabos_sim_contracts'):
        importlib.import_module(name)
    def source_check() -> tuple[dict, list[str]]:
        loaded = {}
        wrong = []
        for name, module in tuple(sys.modules.items()):
            package = name.split('.')[0]
            if package not in ('plc_sim', 'unilabos_sim_contracts') or not getattr(module, '__file__', None):
                continue
            file = Path(module.__file__).resolve()
            suffix = Path(*name.split('.')[1:]) if '.' in name else Path()
            source = (plc if package == 'plc_sim' else core/package)/suffix
            source = source/'__init__.py' if hasattr(module, '__path__') else source.with_suffix('.py')
            loaded[name] = dict(path=str(file), sha256=digest(file), source=str(source))
            if not file.is_relative_to(Path(sys.prefix).resolve()) or 'site-packages' not in file.parts or inputs.get(str(source.resolve())) != loaded[name]['sha256']:
                wrong.append(name)
        return loaded, wrong
    _, wrong = source_check()
    if wrong:
        raise ValueError('已安装模块与固定源码不符: '+repr(wrong))
    import pytest
    selected = ['test_cosimulation.py', 'test_clock_stop.py', 'test_clock_health.py',
                'test_clock_trace.py', 'test_physics_mailbox.py', 'test_source_barrier.py',
                'test_session_host.py', 'test_process_dynamics.py']
    code = pytest.main([str(plc/'tests'/name) for name in selected] +
                      ['-q', '--show-capture=no', '-p', 'no:cacheprovider'])
    loaded, wrong = source_check()
    args.output.write_text(json.dumps(dict(exit=int(code), wrong_sources=wrong,
        input_sha256=inputs, loaded=loaded, core_commit=commit,
        plc_commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
        scope='installed public clock/reference contracts; no GPU, canonical DB or complete ROS qualification'), indent=2))
    return int(code) or bool(wrong)


if __name__ == '__main__':
    raise SystemExit(main())
