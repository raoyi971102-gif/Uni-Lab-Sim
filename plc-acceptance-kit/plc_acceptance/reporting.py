"""生成可追溯的 JSON、JUnit、HTML 与变量时间线报告。"""

from __future__ import annotations

import hashlib
import html
import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .models import AcceptanceBundle, RunResult


def sha256_file(path: str | Path) -> str:
    """计算文件 SHA-256。

    参数：``path`` 是待绑定版本的文件。
    返回：十六进制摘要。
    """

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path, patterns: tuple[str, ...]) -> str:
    """计算一组版本化文件的稳定树指纹。

    参数：``root`` 是验收包根目录，``patterns`` 是需绑定的相对 glob。
    返回：同时包含相对路径和文件内容的 SHA-256 摘要。
    """

    selected = {
        path.resolve()
        for pattern in patterns
        for path in root.glob(pattern)
        if path.is_file()
    }
    digest = hashlib.sha256()
    for path in sorted(selected, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_metadata(root: Path) -> dict[str, str]:
    """读取当前仓库分支和提交身份。

    参数：``root`` 是验收包目录。
    返回：可获取的 ``git_branch`` 与 ``git_commit``；非 Git 环境返回空映射。
    """

    metadata: dict[str, str] = {}
    try:
        metadata["git_commit"] = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        metadata["git_branch"] = subprocess.check_output(
            ["git", "-C", str(root), "branch", "--show-current"], text=True
        ).strip()
        metadata["git_dirty"] = str(
            bool(
                subprocess.check_output(
                    ["git", "-C", str(root), "status", "--short"], text=True
                ).strip()
            )
        ).lower()
    except (OSError, subprocess.SubprocessError):
        return {}
    return metadata


def _runtime_versions() -> dict[str, str]:
    """读取验收运行器和 PLC-Sim 的安装分发版本。

    参数：无。
    返回：存在的分发版本映射；源码未安装时省略对应项。
    """

    versions: dict[str, str] = {}
    for key, distribution in (
        ("acceptance_version", "unilab-plc-acceptance"),
        ("plc_sim_version", "unilab-plc-sim"),
    ):
        try:
            versions[key] = version(distribution)
        except PackageNotFoundError:
            continue
    return versions


def config_fingerprints(
    bundle: AcceptanceBundle,
    *,
    plc_artifact: str | None = None,
) -> dict[str, str]:
    """计算协议、映射、运行时实例、清单、覆盖表、点表和候选包指纹。

    参数：``bundle`` 提供配置路径，``plc_artifact`` 是可选 PLC 候选包。
    返回：按证据名称索引的 SHA-256 映射。
    """

    paths = {
        "protocol": bundle.protocol_path,
        "mapping": bundle.mapping_path,
        "manifest": bundle.manifest_path,
        "requirements_coverage": bundle.coverage_path,
        "environment": bundle.environment_path,
        "plc_csv": bundle.csv_path,
    }
    fingerprints = {name: sha256_file(path) for name, path in paths.items()}
    runtime_mapping = json.dumps(
        {
            "endpoint": bundle.environment.endpoint,
            "namespace_uri": bundle.namespace_uri,
            "node_id_prefix": bundle.node_id_prefix,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    fingerprints["runtime_mapping"] = hashlib.sha256(runtime_mapping).hexdigest()
    fingerprints["acceptance_package"] = sha256_tree(
        bundle.root,
        (
            "pyproject.toml",
            "plc_acceptance/**/*.py",
            "protocol/*.yaml",
            "mappings/*.yaml",
            "environments/*.yaml",
            "simulator/*.yaml",
            "tests/**/*.yaml",
        ),
    )
    fingerprints.update(_runtime_versions())
    fingerprints.update(_git_metadata(bundle.root))
    if plc_artifact:
        artifact_path = Path(plc_artifact).resolve()
        fingerprints["plc_artifact"] = sha256_file(artifact_path)
        fingerprints["plc_artifact_path"] = str(artifact_path)
    return fingerprints


def _write_json(path: Path, payload: Any) -> None:
    """以稳定 UTF-8 格式写入 JSON。

    参数：``path`` 是输出文件，``payload`` 是可序列化对象。
    返回：无。
    """

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def _write_junit(path: Path, result: RunResult) -> None:
    """把运行结果写成 JUnit XML。

    参数：``path`` 是输出文件，``result`` 是完整运行结果。
    返回：无。
    """

    failures = sum(case.status == "FAILED" for case in result.cases)
    skipped = sum(case.status in {"BLOCKED", "ABORTED"} for case in result.cases)
    suite = ET.Element(
        "testsuite",
        name=f"{result.project_id}:{result.environment_id}",
        tests=str(len(result.cases)),
        failures=str(failures),
        skipped=str(skipped),
    )
    for case in result.cases:
        node = ET.SubElement(
            suite,
            "testcase",
            classname=case.case_id,
            name=f"{case.name} [iteration {case.iteration}]",
            time=f"{case.duration_ms / 1000:.6f}",
        )
        if case.status == "FAILED":
            ET.SubElement(node, "failure", message=case.message).text = case.message
        elif case.status in {"BLOCKED", "ABORTED"}:
            ET.SubElement(node, "skipped", message=case.message).text = case.message
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _write_html(path: Path, result: RunResult) -> None:
    """生成无需外部资源的中文 HTML 摘要。

    参数：``path`` 是输出文件，``result`` 是完整运行结果。
    返回：无。
    """

    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(case.case_id)}</td>"
        f"<td>{case.iteration}</td>"
        f"<td>{html.escape(case.safety_level)}</td>"
        f"<td class='{case.status.lower()}'>{html.escape(case.status)}</td>"
        f"<td>{case.duration_ms:.1f}</td>"
        f"<td>{html.escape(case.message)}</td>"
        "</tr>"
        for case in result.cases
    )
    fingerprint_rows = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td><code>{html.escape(value)}</code></td></tr>"
        for name, value in sorted(result.fingerprints.items())
    )
    evidence = result.metadata.get("evidence", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    metadata_values = {
        "OPC UA Endpoint": result.metadata.get("endpoint", ""),
        "Namespace URI": result.metadata.get("namespace_uri", ""),
        "受控测试与安全前置": (
            "已人工确认"
            if result.metadata.get("safe_test_mode_confirmed")
            else "未确认/不适用"
        ),
        "监护/见证人": evidence.get("supervisor", ""),
        "台架/现场位置": evidence.get("test_location", ""),
        "物料或批次标识": evidence.get("material_reference", ""),
    }
    metadata_rows = "\n".join(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(str(value))}</td></tr>"
        for label, value in metadata_values.items()
        if value != ""
    )
    scope_statement = html.escape(str(result.metadata.get("scope_statement", "")))
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>PLC 自动验收报告</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 16px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd3dd;padding:8px;text-align:left}}th{{background:#eef2f7}}.passed{{color:#08783e}}.failed{{color:#b42318}}.blocked,.aborted{{color:#9a6700}}code{{word-break:break-all}}</style></head>
<body><h1>PLC 自动验收报告</h1>
<p>运行 ID：<code>{html.escape(result.run_id)}</code></p>
<p>项目：{html.escape(result.project_id)}；协议版本：{html.escape(result.protocol_version)}；环境：{html.escape(result.environment_id)}；证据级别：{html.escape(result.evidence_level)}</p>
<p>门禁结论：<strong class="{result.status.lower()}">{result.status}</strong></p>
<p><strong>证据边界：</strong>{scope_statement or "仅代表本报告列出的环境与自动清单。"}</p>
<h2>运行现场</h2><table><tbody>{metadata_rows}</tbody></table>
<h2>用例结果</h2><table><thead><tr><th>ID</th><th>轮次</th><th>等级</th><th>状态</th><th>耗时 ms</th><th>诊断</th></tr></thead><tbody>{rows}</tbody></table>
<h2>版本指纹</h2><table><tbody>{fingerprint_rows}</tbody></table>
<p>详细变量证据见同目录 <code>timeline.jsonl</code>，结构化结果见 <code>run.json</code>。</p></body></html>"""
    path.write_text(document, encoding="utf-8")


def write_reports(result: RunResult, output_root: str | Path) -> Path:
    """把一次运行的全部标准证据写入独立目录。

    参数：``result`` 是运行结果，``output_root`` 是报告根目录。
    返回：本次运行目录。
    """

    run_dir = Path(output_root).resolve() / result.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json(run_dir / "run.json", asdict(result))
    with (run_dir / "timeline.jsonl").open("w", encoding="utf-8") as stream:
        for event in result.timeline:
            stream.write(
                json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n"
            )
    _write_junit(run_dir / "junit.xml", result)
    _write_html(run_dir / "report.html", result)
    return run_dir
