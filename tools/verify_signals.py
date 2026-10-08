"""核对安装字节并运行信号回环回归；不启动设备或 GPU。"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('不得覆盖历史回执')
    repo=Path(__file__).resolve().parents[1]
    import plc_sim
    from unilabos_sim_contracts import SourceLock,SourceFile
    installed=Path(plc_sim.__file__).resolve().parent
    assert installed.is_relative_to(Path(sys.prefix).resolve()) and 'site-packages' in installed.parts
    expected=repo/'PLC-Sim'
    inputs={}
    for file in sorted(installed.rglob('*')):
        if file.suffix not in ('.py','.csv'):continue
        relative=file.relative_to(installed)
        assert file.read_bytes()==(expected/relative).read_bytes(),relative
        inputs[str(relative)]=hashlib.sha256(file.read_bytes()).hexdigest()
    commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True)
    lock=SourceLock(source_id='installed-reference',commit=commit,dirty_digest=None,
        files=tuple(SourceFile(path=name,sha256=sha) for name,sha in inputs.items()))
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    sys.dont_write_bytecode=True
    import pytest
    selected=['test_signal_csv_schema.py','test_signal_runtime.py','test_signal_assembly.py','test_reference_signals.py','test_signals_cli_compat.py']
    with tempfile.TemporaryDirectory(prefix='signals-installed-lock-') as directory:
        path=Path(directory)/'source-lock.json';path.write_text(lock.model_dump_json())
        os.environ['PLC_REFERENCE_SOURCE_LOCK']=str(path)
        result=pytest.main(['-q','-p','no:cacheprovider','--import-mode=importlib',*[str(expected/'tests'/name) for name in selected]])
    loaded={};wrong=[]
    for name,module in tuple(sys.modules.items()):
        if not name.startswith('plc_sim') or not getattr(module,'__file__',None):continue
        path=Path(module.__file__).resolve();sha=hashlib.sha256(path.read_bytes()).hexdigest()
        loaded[name]={'path':str(path),'sha256':sha}
        if not path.is_relative_to(installed) or inputs.get(str(path.relative_to(installed)))!=sha:wrong.append(name)
    args.output.write_text(json.dumps({'exit':int(result),'wrong_sources':wrong,'head':commit,'dirty':dirty,'inputs':inputs,'loaded':loaded,'tests':selected,'scope':'Installed public signal loopback/ownership/subscriptions/reference; no device or GPU qualification'},indent=2)+'\n')
    return int(result) or bool(wrong)

if __name__=='__main__':raise SystemExit(main())
