"""CLI for comparing a live dry-run scene/pose to recorded references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openclaw_aruco.reference_gate import compare_reference_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--pose", required=True, type=Path)
    parser.add_argument("--reference-library", required=True, type=Path)
    parser.add_argument("--gate-config", required=True, type=Path)
    parser.add_argument("--scene-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = compare_reference_gate(
        load(args.scene),
        load(args.pose),
        load(args.reference_library),
        load(args.gate_config),
        args.scene_config,
        Path.cwd(),
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "motion_authorized": result["motion_authorized"],
        "tasks": {task: item["status"] for task, item in result["tasks"].items()},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
