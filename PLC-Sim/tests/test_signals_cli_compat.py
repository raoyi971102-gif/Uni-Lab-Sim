"""新增 signals 入口仍保留原产品的独立兼容委托。"""
from __future__ import annotations
import sys
from types import SimpleNamespace
import pytest
from plc_sim import cli

@pytest.mark.parametrize('arguments', [[], ['gui'], ['server','--help'], ['szlab-handshake'], ['handshake'], ['ino'], ['unknown']])
def test_original_commands_keep_exact_legacy_delegation(monkeypatch, arguments):
    calls=[]
    monkeypatch.setitem(sys.modules, 'opcua_sim.cli', SimpleNamespace(main=lambda args: calls.append(args) or 17))
    assert cli.main(arguments)==17
    assert calls==[arguments]

def test_signals_routes_arguments_without_mutating_process_argv(monkeypatch):
    calls=[]
    monkeypatch.setitem(sys.modules, 'plc_sim.signals_cli', SimpleNamespace(main=lambda args: calls.append(args) or 19))
    before=list(sys.argv)
    assert cli.main(['signals','--steps','2'])==19
    assert calls==[['--steps','2']] and sys.argv==before
