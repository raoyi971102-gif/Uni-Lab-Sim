from __future__ import annotations

import pytest

from common import NodeDef
from gui.backend import ServerVariableWriteReq, _replace_array_element


def _array_node(data_type: str = "BOOLEAN", length: int = 3) -> NodeDef:
    return NodeDef(
        "ArrayNode", "", "VARIABLE", data_type, "ns=4;s=ArrayNode",
        array_len=length,
    )


def test_replace_array_element_preserves_native_array_shape() -> None:
    current = [False, False, True]
    updated = _replace_array_element(_array_node(), current, 1, "true")

    assert updated == [False, True, True]
    assert current == [False, False, True]


def test_replace_array_element_coerces_numeric_value() -> None:
    updated = _replace_array_element(
        _array_node(data_type="DOUBLE", length=2), [1.0, 2.0], 0, "3.25"
    )
    assert updated == [3.25, 2.0]


@pytest.mark.parametrize(
    ("node", "current", "index", "message"),
    [
        (_array_node(length=0), [], 0, "不是数组节点"),
        (_array_node(length=3), [False], 0, "在线数组长度异常"),
        (_array_node(length=3), [False, False, False], 3, "数组下标必须"),
    ],
)
def test_replace_array_element_rejects_invalid_shape_or_index(
    node: NodeDef, current: list[object], index: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _replace_array_element(node, current, index, True)


def test_array_element_write_request_uses_zero_based_index() -> None:
    request = ServerVariableWriteReq(node_id="ns=4;s=ArrayNode", index=2, value=False)
    assert request.index == 2
