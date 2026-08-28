from __future__ import annotations

from ino_mcp.extractor import extract_gvl_variables


def _gvl_block(path: str) -> str:
    return "\n".join(
        (
            "===DECL_BEGIN===",
            f"PATH: {path}",
            "IMPL: 0",
            "MIXIN: <gvl>",
            "---BODY---",
            "VAR_GLOBAL",
            "    Value : BOOL;",
            "END_VAR",
            "===DECL_END===",
        )
    )


class _FakeToolkit:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def dump_all_declarations(self) -> str:
        return "\n".join(
            (
                _gvl_block("Application/20_变量Date/GVL"),
                _gvl_block("Application/20_变量Date/IO"),
                _gvl_block("Application/20_变量Date/HMI_Date"),
            )
        )

    def get_project_structure(self) -> str:
        raise AssertionError("declaration discovery should not use structure text")

    def read_gvl_declaration(self, path: str) -> str:
        self.paths.append(path)
        return "VAR_GLOBAL\n    Value : BOOL;\nEND_VAR"


def test_automatic_discovery_includes_gvls_without_gvl_in_their_names() -> None:
    toolkit = _FakeToolkit()

    leaves = extract_gvl_variables(
        toolkit, gvl_paths=None, include_all=True, auto_build_dut_registry=True
    )

    assert toolkit.paths == [
        "Application/20_变量Date/GVL",
        "Application/20_变量Date/IO",
        "Application/20_变量Date/HMI_Date",
    ]
    assert [leaf.name for leaf in leaves] == ["Value", "Value", "Value"]
