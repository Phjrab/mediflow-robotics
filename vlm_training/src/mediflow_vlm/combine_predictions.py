from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .dataset import load_jsonl, write_jsonl
from .schema import validate_action_completion, validate_grasp_completion


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Combine orientation from an action adapter with a grasp-only adapter."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--action-predictions", type=Path, required=True)
    parser.add_argument("--grasp-predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def combine_predictions(
    action_rows: list[dict[str, Any]], grasp_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grasp_by_id = {row["id"]: row for row in grasp_rows}
    combined: list[dict[str, Any]] = []
    for action_row in action_rows:
        sample_id = action_row["id"]
        grasp_row = grasp_by_id.get(sample_id)
        errors: list[str] = []
        action = action_row.get("prediction")
        action_valid, action_errors = validate_action_completion(action)
        if not action_valid:
            errors.extend(f"action adapter: {error}" for error in action_errors)
        grasp = grasp_row.get("prediction") if grasp_row else None
        grasp_valid, grasp_errors = validate_grasp_completion(grasp)
        if grasp_row is None:
            errors.append("grasp adapter: missing prediction")
        elif not grasp_valid:
            errors.extend(f"grasp adapter: {error}" for error in grasp_errors)

        prediction = None
        if not errors:
            prediction = {
                "orientation": action["orientation"],
                "grasp_region": grasp["grasp_region"],
            }
        combined.append(
            {
                "id": sample_id,
                "model": {
                    "orientation": action_row.get("model"),
                    "grasp_region": grasp_row.get("model") if grasp_row else None,
                },
                "prediction": prediction,
                "valid": not errors,
                "errors": errors,
                "component_predictions": {
                    "action_adapter": action,
                    "grasp_adapter": grasp,
                },
            }
        )
    return combined


def main() -> None:
    args = build_parser().parse_args()
    root = args.project_root.resolve()
    resolve = lambda path: path if path.is_absolute() else root / path
    combined = combine_predictions(
        load_jsonl(resolve(args.action_predictions)),
        load_jsonl(resolve(args.grasp_predictions)),
    )
    write_jsonl(resolve(args.output), combined)
    print(f"wrote {len(combined)} combined predictions to {resolve(args.output)}")


if __name__ == "__main__":
    main()
