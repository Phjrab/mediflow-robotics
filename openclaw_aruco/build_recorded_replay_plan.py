"""CLI for a non-executable, step-limited recorded episode plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openclaw_aruco.recorded_replay_plan import build_recorded_replay_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--episode", required=True, type=int)
    parser.add_argument("--task", required=True, choices=("A", "B", "C"))
    parser.add_argument("--pose", required=True, type=Path)
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = build_recorded_replay_plan(
        args.dataset, args.episode, load(args.pose), args.task, load(args.scene)
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "motion_authorized": result["motion_authorized"],
        "episode": result["source"]["episode"],
        "generated_targets": result["control"]["generated_targets"],
        "estimated_duration_seconds": result["control"]["estimated_duration_seconds"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
