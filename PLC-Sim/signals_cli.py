"""显式锁定工厂向正式 CLI 注入原 CoupledSession 和信号目录。"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import time
from unilabos_sim_contracts import SourceLock
from .model_loading import ModelSource, load_symbol
from .signal_assembly import SignalAssembly


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--factory',required=True,help='锁定设备包 module:factory，返回 SignalAssembly')
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--source-lock',type=Path,required=True)
    parser.add_argument('--steps',type=int,default=None)
    args=parser.parse_args(argv)
    if args.steps is not None and args.steps<1:parser.error('--steps 必须为正整数')
    root=args.source_root.resolve(strict=True)
    lock=SourceLock.model_validate_json(args.source_lock.read_text(encoding='utf-8'))
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    dirty=subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=root,text=True)
    if dirty or lock.dirty_digest is not None:raise ValueError('本 CLI 只准入已提交来源；dirty 候选须使用显式装配 API 并单独归因')
    source=ModelSource(lock,root,commit,None)
    sys.path.insert(0,str(root))
    assembly=None
    try:
        factory=load_symbol(args.factory,source=source)
        assembly=factory(source=source)
        if not isinstance(assembly,SignalAssembly):raise ValueError('工厂未提供原会话信号装配')
        assembly.start();assembly.session.resume()
        count=0
        while args.steps is None or count<args.steps:
            frame=assembly.session.step();assembly.collect(frame);count+=1
            if args.steps is None:time.sleep(assembly.session.wall_delay())
    except KeyboardInterrupt:
        pass
    finally:
        if isinstance(assembly,SignalAssembly):
            if assembly.session.state=='running':assembly.session.pause()
            assembly.close()
        sys.path.remove(str(root))
    return 0
