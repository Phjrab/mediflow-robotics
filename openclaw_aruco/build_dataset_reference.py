"""CLI for creating a non-executable A/B/C dataset reference library."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openclaw_aruco.dataset_reference import build_reference_library


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        parser.error(f"output already exists: {args.output}; pass --force to replace it")
    root = Path.cwd()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_reference_library(config, root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "motion_authorized": result["motion_authorized"],
        "output": str(args.output),
        "usable_reference_episodes": {
            task: item["dataset"]["usable_reference_episodes"] for task, item in result["tasks"].items()
        },
        "quality_rejected_episodes": {
            task: item["dataset"]["quality_rejected_episodes"] for task, item in result["tasks"].items()
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
