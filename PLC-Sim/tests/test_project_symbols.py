from __future__ import annotations

import pytest

from ino_mcp.symbols import parse_symbols, set_symbol_pragma


DECLARATION = """VAR_GLOBAL
    PlainValue : BOOL;
    {attribute 'symbol' := 'readwrite'}
    ExportedValue : REAL;
END_VAR
"""


def test_symbol_pragma_round_trip_preserves_other_declarations() -> None:
    symbols = parse_symbols(DECLARATION)
    assert [(item["name"], item["exported"]) for item in symbols] == [
        ("PlainValue", False), ("ExportedValue", True),
    ]
    enabled = set_symbol_pragma(DECLARATION, "PlainValue", True)
    assert parse_symbols(enabled)[0]["exported"] is True
    disabled = set_symbol_pragma(enabled, "PlainValue", False)
    assert disabled == DECLARATION


def test_symbol_pragma_rejects_unknown_variable() -> None:
    with pytest.raises(ValueError, match="未找到变量"):
        set_symbol_pragma(DECLARATION, "Missing", True)

