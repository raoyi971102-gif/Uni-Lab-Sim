from __future__ import annotations

import json
import subprocess
from pathlib import Path


LAUNCHER = (
    Path(__file__).parents[1]
    / "vendor"
    / "inoproshop-mcp"
    / "persistent-launcher.js"
)


def test_launcher_keeps_generated_project_handles_open() -> None:
    source = "project.close()\nprimary_project.close()\nother.close()"
    program = (
        "const m=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(m.transformScript(process.argv[2])));"
    )

    result = subprocess.run(
        ["node", "-e", program, str(LAUNCHER), source],
        check=True,
        capture_output=True,
        text=True,
    )

    transformed = json.loads(result.stdout)
    assert transformed == (
        "_opcuasim_keep_project_open(project)\n"
        "_opcuasim_keep_project_open(primary_project)\n"
        "other.close()"
    )


def test_launcher_extracts_bundle_result_path() -> None:
    source = "_RESULT_FILE = r'C:\\\\Temp\\\\result.txt'"
    program = (
        "const m=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(m.extractResultPath(process.argv[2])));"
    )

    result = subprocess.run(
        ["node", "-e", program, str(LAUNCHER), source],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == r"C:\\Temp\\result.txt"
