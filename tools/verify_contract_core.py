"""从独立 wheel 环境核对公共核心来源并运行其回归。"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import pytest

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    loaded = {}
    for name in ('plc_sim', 'plc_sim.cosimulation', 'plc_sim.model_loading', 'plc_sim.physics_mailbox'):
        module = importlib.import_module(name)
        file = Path(module.__file__).resolve()
        relative = '__init__.py' if name == 'plc_sim' else name.split('.')[-1]+'.py'
        expected = root/'PLC-Sim'/relative
        assert file.is_relative_to(Path(sys.prefix).resolve()), (name, file)
        assert 'site-packages' in file.parts, (name, file)
        assert file.read_bytes() == expected.read_bytes(), (name, file)
        loaded[name] = {'path':str(file), 'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    forbidden = ('opcua_sim','opcua','isaacsim','omni','rclpy','szlab_poly_studio','eit_ptlc')
    assert not any(n == p or n.startswith(p+'.') for n in sys.modules for p in forbidden)
    files = ['test_model_loading.py','test_model_source_contract.py','test_contract_authority.py','test_cosimulation.py','test_physics_mailbox.py']
    result = pytest.main(['-q','--import-mode=importlib','--rootdir='+str(root/'PLC-Sim/tests'),'-p','no:cacheprovider',*[str(root/'PLC-Sim/tests'/f) for f in files]])
    report = {'exitcode':int(result),'python':sys.version,'loaded':loaded,'dependencies':{name:importlib.metadata.version(name) for name in ('unilab-plc-sim','unilab-opcua-sim','unilabos-sim-contracts','pydantic','pytest')},'tests':files,'scope':'Installed public core only; no SDK or full device qualification'}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return int(result)

if __name__ == '__main__':
    raise SystemExit(main())
