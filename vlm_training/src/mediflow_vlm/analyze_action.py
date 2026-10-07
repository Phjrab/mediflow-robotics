from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from .dataset import load_jsonl, write_jsonl
from .safety import camera_family


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slice action-model errors by camera and phase.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--errors", type=Path, required=True)
    return parser


def _slice(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    total = len(rows)
    orientation = sum(
        predicted.get("orientation") == row["completion"]["orientation"]
        for row, predicted in rows
    )
    grasp = sum(
        predicted.get("grasp_region") == row["completion"]["grasp_region"]
        for row, predicted in rows
    )
    exact = sum(
        predicted.get("orientation") == row["completion"]["orientation"]
        and predicted.get("grasp_region") == row["completion"]["grasp_region"]
        for row, predicted in rows
    )
    return {
        "samples": total,
        "exact_accuracy": exact / total if total else 0.0,
        "orientation_accuracy": orientation / total if total else 0.0,
        "grasp_accuracy": grasp / total if total else 0.0,
        "expected_grasp": dict(
            sorted(Counter(row["completion"]["grasp_region"] for row, _ in rows).items())
        ),
        "predicted_grasp": dict(
            sorted(Counter(predicted.get("grasp_region") for _, predicted in rows).items())
        ),
    }


def analyze(
    truth_rows: list[dict[str, Any]], prediction_rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predictions = {row["id"]: row.get("prediction") or {} for row in prediction_rows}
    paired = [(row, predictions.get(row["id"], {})) for row in truth_rows]
    dimensions: dict[str, Callable[[dict[str, Any]], str]] = {
        "camera": lambda row: camera_family(row.get("camera")),
        "capture_phase": lambda row: str(row.get("source", {}).get("capture_phase")),
        "source": lambda row: Path(row["image"]).parts[1],
    }
    slices: dict[str, Any] = {}
    for name, getter in dimensions.items():
        groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for row, predicted in paired:
            groups[getter(row)].append((row, predicted))
        slices[name] = {key: _slice(values) for key, values in sorted(groups.items())}

    errors = []
    for row, predicted in paired:
        expected = {
            key: row["completion"][key] for key in ("orientation", "grasp_region")
        }
        wrong = [key for key in expected if predicted.get(key) != expected[key]]
        if wrong:
            errors.append(
                {
                    "id": row["id"],
                    "image": row["image"],
                    "camera": row.get("camera"),
                    "capture_phase": row.get("source", {}).get("capture_phase"),
                    "expected": expected,
                    "predicted": predicted,
                    "wrong_fields": wrong,
                }
            )
    return {"overall": _slice(paired), "slices": slices}, errors


def main() -> None:
    args = build_parser().parse_args()
    root = args.project_root.resolve()
    resolve = lambda value: value if value.is_absolute() else root / value
    report, errors = analyze(
        load_jsonl(resolve(args.manifest)), load_jsonl(resolve(args.predictions))
    )
    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(resolve(args.errors), errors)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
