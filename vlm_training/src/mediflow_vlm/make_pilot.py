from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .dataset import load_jsonl, write_jsonl


PILOT_ORIENTATIONS = {"upright", "fallen"}


def select_pilot_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Keep clear before-grasp examples while preserving the source manifests."""
    selected: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    for row in rows:
        if row.get("source", {}).get("capture_phase") != "before_grasp":
            excluded["not_before_grasp"] += 1
        elif row.get("completion", {}).get("orientation") not in PILOT_ORIENTATIONS:
            excluded["orientation_not_upright_or_fallen"] += 1
        elif row.get("near_duplicate_of") is not None or "near_duplicate_candidate" in row.get(
            "qa_flags", []
        ):
            excluded["near_duplicate_candidate"] += 1
        else:
            selected.append(row)
    return selected, excluded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create conservative pilot manifests without changing source data or labels."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset_v2"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    dataset_dir = args.dataset_dir
    if not dataset_dir.is_absolute():
        dataset_dir = project_root / dataset_dir

    report: dict[str, Any] = {
        "purpose": "pipeline-validation-only",
        "review_status": "preserved from source; pending rows require explicit training override",
        "selection": {
            "capture_phase": "before_grasp",
            "orientations": sorted(PILOT_ORIENTATIONS),
            "exclude_near_duplicates": True,
        },
        "splits": {},
    }
    for split in ("train", "validation", "test"):
        source_path = dataset_dir / f"{split}.jsonl"
        selected, excluded = select_pilot_rows(load_jsonl(source_path))
        output_path = dataset_dir / f"pilot_{split}.jsonl"
        write_jsonl(output_path, selected)
        report["splits"][split] = {
            "source_samples": len(selected) + sum(excluded.values()),
            "selected_samples": len(selected),
            "excluded": dict(sorted(excluded.items())),
            "medicine_id": dict(sorted(Counter(r["completion"]["medicine_id"] for r in selected).items())),
            "orientation": dict(sorted(Counter(r["completion"]["orientation"] for r in selected).items())),
            "sessions": len({r["session_id"] for r in selected}),
            "output": str(output_path.relative_to(project_root)),
        }

    report_path = dataset_dir / "pilot_selection_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
