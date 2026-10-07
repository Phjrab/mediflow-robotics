from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .dataset import load_jsonl
from .schema import (
    validate_action_completion,
    validate_completion,
    validate_grasp_completion,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate VLM JSON predictions.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=Path("dataset_v2/test.jsonl"))
    parser.add_argument("--predictions", type=Path, default=Path("dataset_v2/predictions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("dataset_v2/evaluation.json"))
    parser.add_argument("--task", choices=("full", "action", "grasp"), default="full")
    return parser


def evaluate(
    truth_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    task: str = "full",
) -> dict[str, Any]:
    truth = {row["id"]: row["completion"] for row in truth_rows}
    predictions = {row["id"]: row for row in prediction_rows}
    require_grasp_region = any("grasp_region" in value for value in truth.values())
    if task == "action":
        fields = ("orientation", "grasp_region")
    elif task == "grasp":
        fields = ("grasp_region",)
    elif task == "full":
        fields = ("medicine_id", "orientation", "target_bin") + (
            ("grasp_region",) if require_grasp_region else ()
        )
    else:
        raise ValueError(f"unsupported evaluation task: {task}")
    correct = Counter()
    expected_counts: dict[str, Counter[str]] = {field: Counter() for field in fields}
    predicted_counts: dict[str, Counter[str]] = {field: Counter() for field in fields}
    confusion: dict[str, Counter[str]] = {field: Counter() for field in fields}
    invalid = 0
    missing = 0
    exact = 0
    wrong_bin = 0
    for sample_id, expected in truth.items():
        row = predictions.get(sample_id)
        if row is None:
            missing += 1
            continue
        predicted = row.get("prediction")
        if task == "action":
            valid, _ = validate_action_completion(predicted)
        elif task == "grasp":
            valid, _ = validate_grasp_completion(predicted)
        else:
            valid, _ = validate_completion(
                predicted, require_grasp_region=require_grasp_region
            )
        if not valid:
            invalid += 1
            continue
        for field in fields:
            actual_value = predicted[field]
            expected_value = expected[field]
            expected_counts[field][expected_value] += 1
            predicted_counts[field][actual_value] += 1
            confusion[field][f"{expected_value} -> {actual_value}"] += 1
            if actual_value == expected_value:
                correct[field] += 1
        if all(predicted[field] == expected[field] for field in fields):
            exact += 1
        if task == "full" and predicted["target_bin"] != expected["target_bin"]:
            wrong_bin += 1

    denominator = len(truth_rows)
    covered = denominator - missing
    class_recall: dict[str, dict[str, float]] = {}
    balanced_accuracy: dict[str, float] = {}
    for field in fields:
        recalls = {}
        for value, count in sorted(expected_counts[field].items()):
            recalls[value] = confusion[field][f"{value} -> {value}"] / count
        class_recall[field] = recalls
        balanced_accuracy[field] = sum(recalls.values()) / len(recalls) if recalls else 0.0
    return {
        "task": task,
        "samples": denominator,
        "prediction_records": len(prediction_rows),
        "covered_samples": covered,
        "coverage": covered / denominator if denominator else 0.0,
        "missing_predictions": missing,
        "invalid_predictions": invalid,
        "exact_match_accuracy": exact / covered if covered else 0.0,
        "field_accuracy": {
            field: correct[field] / covered if covered else 0.0 for field in fields
        },
        "field_balanced_accuracy": balanced_accuracy,
        "class_recall": class_recall,
        "predicted_distribution": {
            field: dict(sorted(values.items())) for field, values in predicted_counts.items()
        },
        "full_test_field_accuracy": {
            field: correct[field] / denominator if denominator else 0.0 for field in fields
        },
        "wrong_bin_rate": wrong_bin / covered if covered else 0.0,
        "confusion": {
            field: dict(sorted(values.items())) for field, values in confusion.items()
        },
    }


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else project_root / args.manifest
    predictions_path = (
        args.predictions if args.predictions.is_absolute() else project_root / args.predictions
    )
    output_path = args.output if args.output.is_absolute() else project_root / args.output
    report = evaluate(
        load_jsonl(manifest_path), load_jsonl(predictions_path), task=args.task
    )
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
