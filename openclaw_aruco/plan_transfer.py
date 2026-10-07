#!/usr/bin/env python3
"""Create a non-executable A/B/C transfer plan from a saved scene report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openclaw_aruco.planner import plan_transfer
from openclaw_aruco.scene import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task", required=True, choices=("A", "B", "C"))
    parser.add_argument("--json-output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.json_output.exists():
        raise ValueError(f"output already exists: {args.json_output}")
    if not args.json_output.parent.is_dir():
        raise ValueError(f"output parent does not exist: {args.json_output.parent}")
    scene = json.loads(args.scene.read_text(encoding="utf-8"))
    config = load_config(args.config)
    report = plan_transfer(scene, config, args.task)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
