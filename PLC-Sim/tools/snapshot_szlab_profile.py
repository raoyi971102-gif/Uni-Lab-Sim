#!/usr/bin/env python3
"""从 Uni-Lab-SZLab Catalog 生成或校验 PLC-SIM 行为覆盖清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from unilabos.package_manager import WorkspaceSource, compile_package_source


def catalog_snapshot(root: Path) -> dict:
    """编译设备包并返回排除 ``_sim`` 镜像设备后的真实动作目录。"""

    catalog = compile_package_source(WorkspaceSource(root.resolve()))
    devices: dict[str, list[str]] = {}
    for definition in catalog.definitions.devices:
        device_id = definition.fqid.rsplit(".", maxsplit=1)[-1]
        if device_id.endswith("_sim"):
            continue
        registry = definition.details["registry_entry"]["class"]
        devices[device_id] = sorted(registry["action_value_mappings"])
    return {
        "package_id": catalog.namespace,
        "real_devices": len(devices),
        "real_actions": sum(len(actions) for actions in devices.values()),
        "workflows": len(catalog.definitions.workflows),
        "devices": dict(sorted(devices.items())),
    }


def behavior_actions(path: Path) -> dict[str, list[str]]:
    """把 PLC-SIM 分类 YAML 归一为逐设备动作目录。"""

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, list[str]] = {}
    for device_id, groups in dict(payload.get("devices", {})).items():
        actions = {
            str(action)
            for entries in dict(groups or {}).values()
            for action in (entries or ())
        }
        result[str(device_id)] = sorted(actions)
    return dict(sorted(result.items()))


def behavior_metadata(path: Path) -> dict[str, object]:
    """返回分类 YAML 声明的包身份与 Catalog 计数。"""

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    summary = dict(payload.get("catalog_summary", {}))
    return {
        "package_id": payload.get("package_id"),
        "real_devices": summary.get("real_devices"),
        "real_actions": summary.get("real_actions"),
        "workflows": summary.get("workflows"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_root", type=Path)
    parser.add_argument("--behavior", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    snapshot = catalog_snapshot(args.reference_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))

    if args.behavior:
        declared = behavior_actions(args.behavior)
        declared_metadata = behavior_metadata(args.behavior)
        actual_metadata = {
            key: snapshot[key]
            for key in ("package_id", "real_devices", "real_actions", "workflows")
        }
        if declared != snapshot["devices"] or declared_metadata != actual_metadata:
            missing = {
                device: sorted(set(actions) - set(declared.get(device, ())))
                for device, actions in snapshot["devices"].items()
                if set(actions) - set(declared.get(device, ()))
            }
            stale = {
                device: sorted(set(actions) - set(snapshot["devices"].get(device, ())))
                for device, actions in declared.items()
                if set(actions) - set(snapshot["devices"].get(device, ()))
            }
            print(
                json.dumps(
                    {
                        "metadata": {
                            "declared": declared_metadata,
                            "actual": actual_metadata,
                        },
                        "missing": missing,
                        "stale": stale,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
