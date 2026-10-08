"""共享CSV解析保持原标量协议类型，不加载设备包。"""
from pathlib import Path
from plc_sim.common import load_csv


def test_canonical_scalar_types(tmp_path: Path) -> None:
    names=('BOOLEAN','BYTE','INT16','INT32','FLOAT','DOUBLE','STRING')
    path=tmp_path/'types.csv'
    path.write_text('Name,EnglishName,NodeType,DataType,NodeId\n'+''.join(
        f'{name},{name},VARIABLE,{name},ns=2;s={name}\n' for name in names),encoding='utf-8')
    nodes=load_csv(path)
    assert tuple(node.data_type for node in nodes)==names
    assert tuple(node.node_id for node in nodes)==tuple(f'ns=2;s={name}' for name in names)


def test_export_scalar_aliases_keep_explicit_addresses(tmp_path: Path) -> None:
    names={'BOOL':'BOOLEAN','INT':'INT16','DINT':'INT32','REAL':'FLOAT','STRING':'STRING'}
    path=tmp_path/'export.csv'
    path.write_text('变量名\t数据类型\tNodeId\n'+''.join(
        f'{name}\t{name}\tns=4;s=selected.{name}\n' for name in names),encoding='utf-16')
    nodes=load_csv(path)
    assert tuple(node.data_type for node in nodes)==tuple(names.values())
    assert tuple(node.node_id for node in nodes)==tuple(f'ns=4;s=selected.{name}' for name in names)
