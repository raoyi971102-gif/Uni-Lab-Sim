"""从只读 PTLC 参考节点表生成 PLC-Sim 的最小协议快照。"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--behavior-source", type=Path)
    parser.add_argument("--behavior-destination", type=Path)
    args = parser.parse_args()
    source_text = args.source.read_text(encoding="utf-8")
    if "gvl_path:" not in source_text or "nodes:" not in source_text:
        raise ValueError("source is not a PTLC plc_nodes.yaml file")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        "# Generated PTLC V2 protocol snapshot; do not edit by hand.\n"
        + "# Refresh with tools/snapshot_ptlc_profile.py.\n"
        + source_text,
        encoding="utf-8",
    )
    if bool(args.behavior_source) != bool(args.behavior_destination):
        raise ValueError("behavior-source 与 behavior-destination 必须同时提供")
    if args.behavior_source:
        names = (
            "sampling.yaml", "collect.yaml", "develop.yaml", "photoscrape.yaml",
            "feedlift.yaml", "pump.yaml", "rail.yaml", "staging_a.yaml",
        )
        args.behavior_destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            source = args.behavior_source / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copyfile(source, args.behavior_destination / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
