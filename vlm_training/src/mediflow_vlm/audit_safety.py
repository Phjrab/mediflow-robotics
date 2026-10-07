from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .dataset import load_jsonl, write_jsonl
from .safety import assess_action_safety
from .schema import compose_command_action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit action predictions with fail-closed policy.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    return parser


def audit(
    truth_rows: list[dict[str, Any]], prediction_rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predictions = {row["id"]: row for row in prediction_rows}
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    queue: list[dict[str, Any]] = []
    motion_allowed = 0
    invalid = 0
    for row in truth_rows:
        prediction_row = predictions.get(row["id"], {})
        action = prediction_row.get("prediction")
        policy = assess_action_safety(
            action,
            camera=row.get("camera"),
            capture_phase=row.get("source", {}).get("capture_phase"),
        )
        status_counts[policy["status"]] += 1
        motion_allowed += int(policy["robot_motion_allowed"])
        reason_counts.update(policy["reasons"])
        decision = None
        try:
            decision = compose_command_action(
                row["completion"]["medicine_id"], action
            )
        except Exception:
            invalid += 1
        expected_action = {
            key: row["completion"][key] for key in ("orientation", "grasp_region")
        }
        queue.append(
            {
                "id": row["id"],
                "image": row["image"],
                "camera": row.get("camera"),
                "capture_phase": row.get("source", {}).get("capture_phase"),
                "decision": decision,
                "expected_action": expected_action,
                "prediction_correct": action == expected_action,
                "safety": policy,
            }
        )
    report = {
        "samples": len(truth_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "invalid_decisions": invalid,
        "robot_motion_allowed": motion_allowed,
        "policy": "fail-closed; inference never authorizes robot motion",
    }
    return report, queue


def main() -> None:
    args = build_parser().parse_args()
    root = args.project_root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    report, queue = audit(
        load_jsonl(resolve(args.manifest)), load_jsonl(resolve(args.predictions))
    )
    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(resolve(args.queue), queue)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
