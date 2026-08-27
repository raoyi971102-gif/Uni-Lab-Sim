from __future__ import annotations

from common import (
    NodeDef,
    connection_state_path,
    load_csv,
    node_defs_fingerprint,
    runtime_data_dir,
)


def test_load_utf16_tab_separated_szlab_csv(tmp_path):
    csv_path = tmp_path / "szlab.csv"
    csv_path.write_text(
        "\t".join(["序号", "变量名", "数据类型", "初始值", "node_id"])
        + "\n"
        + "\t".join(["1", "Robot_Home", "BOOL", "OFF", ""])
        + "\n"
        + "\t".join(["2", "任务号", "DINT", "0", "ns=4;s=上位机通讯|任务号"])
        + "\n"
        + "\t".join(["3", "S09天平读数", "REAL", "0.0", ""])
        + "\n"
        + "\t".join(["4", "结构数组", "INT[30]", "...", ""])
        + "\n",
        encoding="utf-16",
    )

    nodes = load_csv(csv_path)

    assert [(node.name_cn, node.data_type) for node in nodes] == [
        ("Robot_Home", "BOOLEAN"),
        ("任务号", "INT32"),
        ("S09天平读数", "FLOAT"),
    ]
    assert nodes[0].node_id == "ns=4;s=上位机通讯|Robot_Home"
    assert nodes[1].node_id == "ns=4;s=上位机通讯|任务号"


def test_node_defs_fingerprint_is_semantic_and_order_independent():
    first = NodeDef("变量A", "A", "VARIABLE", "BOOLEAN", "ns=4;s=变量A")
    second = NodeDef("变量B", "B", "VARIABLE", "INT32", "ns=4;s=变量B")

    assert node_defs_fingerprint([first, second]) == node_defs_fingerprint(
        [second, first]
    )
    assert node_defs_fingerprint([first]) != node_defs_fingerprint([second])


def test_runtime_data_directory_can_be_overridden(monkeypatch, tmp_path):
    runtime_root = tmp_path / "plc-sim-state"
    monkeypatch.setenv("PLCSIM_DATA_DIR", str(runtime_root))

    assert runtime_data_dir() == runtime_root.resolve()
    assert connection_state_path() == (
        runtime_root / "runtime" / "server-connections.json"
    ).resolve()
