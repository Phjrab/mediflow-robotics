from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .dataset import _dhash, write_jsonl
from .review_store import GRASP_REGIONS, ReviewStore
from .schema import validate_completion


ALLOWED_PHASES = frozenset({"before_grasp", "after_grasp"})
ALLOWED_ORIENTATIONS = frozenset({"upright", "fallen"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a leakage-safe four-field dataset from approved reviews."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--config", type=Path, default=Path("vlm_training/config/vlm_v3.yaml")
    )
    return parser


def _camera_family(camera: str | None) -> str:
    value = str(camera or "unknown").lower()
    for name in ("realsense", "astra", "video4", "video6"):
        if name in value:
            return name
    return value


def _features(rows: Iterable[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        completion = row["completion"]
        counts["total"] += 1
        counts[f"medicine:{completion['medicine_id']}"] += 1
        counts[f"orientation:{completion['orientation']}"] += 1
        counts[f"grasp:{completion['grasp_region']}"] += 1
        counts[f"camera:{_camera_family(row.get('camera'))}"] += 1
        counts[f"phase:{row['source']['capture_phase']}"] += 1
        counts[
            "combo:"
            + ":".join(
                (
                    completion["medicine_id"],
                    completion["orientation"],
                    completion["grasp_region"],
                )
            )
        ] += 1
    return counts


def split_reviewed_rows(
    rows: list[dict[str, Any]], fractions: dict[str, float], seed: int
) -> dict[str, str]:
    if not math.isclose(sum(fractions.values()), 1.0, abs_tol=1e-9):
        raise ValueError("split fractions must sum to 1.0")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["session_id"]].append(row)

    rng = random.Random(seed)
    groups = list(grouped.items())
    rng.shuffle(groups)
    groups.sort(key=lambda item: len(item[1]), reverse=True)
    global_counts = _features(rows)
    split_counts = {name: Counter() for name in fractions}
    assignment: dict[str, str] = {}

    def cost(candidate: str, addition: Counter[str]) -> float:
        score = 0.0
        for split, fraction in fractions.items():
            counts = split_counts[split].copy()
            if split == candidate:
                counts.update(addition)
            for feature, total in global_counts.items():
                target = max(total * fraction, 1.0)
                if feature == "total":
                    weight = 3.0
                elif feature.startswith("combo:"):
                    weight = 1.5
                else:
                    weight = 1.0
                score += weight * ((counts[feature] - target) / target) ** 2
        return score

    for session_id, group_rows in groups:
        addition = _features(group_rows)
        choices = list(fractions)
        rng.shuffle(choices)
        chosen = min(choices, key=lambda name: cost(name, addition))
        assignment[session_id] = chosen
        split_counts[chosen].update(addition)

    empty = [name for name, counts in split_counts.items() if not counts["total"]]
    if empty:
        raise ValueError(f"grouped split produced empty partitions: {empty}")
    return assignment


def approved_rows(store: ReviewStore, prompt: str) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    for source in store.rows:
        item = store.effective_item(source["id"])
        phase = item.get("capture_phase")
        if item["review_status"] != "approved":
            excluded[f"review_status:{item['review_status']}"] += 1
            continue
        if phase not in ALLOWED_PHASES:
            excluded[f"capture_phase:{phase}"] += 1
            continue
        completion = deepcopy(item["completion"])
        if completion.get("orientation") not in ALLOWED_ORIENTATIONS:
            excluded[f"orientation:{completion.get('orientation')}"] += 1
            continue
        if item.get("grasp_region") not in GRASP_REGIONS:
            excluded["grasp_region:missing"] += 1
            continue
        completion["grasp_region"] = item["grasp_region"]
        valid, errors = validate_completion(completion, require_grasp_region=True)
        if not valid:
            raise ValueError(f"invalid approved completion for {source['id']}: {errors}")
        row = deepcopy(source)
        row["prompt"] = prompt
        row["completion"] = completion
        row["review_status"] = "approved"
        row["source_split"] = row.pop("split", None)
        row["review"] = {
            "decided_at": item["decided_at"],
            "note": item["note"],
        }
        rows.append(row)
    return rows, excluded


def deduplicate_rows(
    rows: list[dict[str, Any]], project_root: Path, threshold: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate only identical-label frames within one session and phase."""
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        completion = row["completion"]
        key = (
            row["session_id"],
            row["source"]["capture_phase"],
            completion["medicine_id"],
            completion["orientation"],
            completion["target_bin"],
            completion["grasp_region"],
        )
        grouped[key].append(row)

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        group_kept: list[tuple[dict[str, Any], int]] = []
        for row in sorted(group_rows, key=lambda value: value["captured_at"]):
            image_hash = _dhash(project_root / row["image"])
            match = next(
                (
                    previous
                    for previous, previous_hash in group_kept
                    if (image_hash ^ previous_hash).bit_count() <= threshold
                ),
                None,
            )
            if match is None:
                group_kept.append((row, image_hash))
                kept.append(row)
            else:
                removed.append(
                    {
                        "id": row["id"],
                        "near_duplicate_of": match["id"],
                        "session_id": row["session_id"],
                        "capture_phase": row["source"]["capture_phase"],
                    }
                )
    kept.sort(key=lambda row: (row["captured_at"], row["id"]))
    return kept, removed


def _distribution(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    fields = ("medicine_id", "orientation", "target_bin", "grasp_region")
    return {
        "samples": len(materialized),
        "sessions": len({row["session_id"] for row in materialized}),
        "fields": {
            field: dict(sorted(Counter(row["completion"][field] for row in materialized).items()))
            for field in fields
        },
        "capture_phase": dict(
            sorted(Counter(row["source"]["capture_phase"] for row in materialized).items())
        ),
        "camera": dict(
            sorted(Counter(_camera_family(row.get("camera")) for row in materialized).items())
        ),
    }


def prepare(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    dataset_config = config["dataset"]
    store = ReviewStore(
        project_root,
        Path(dataset_config["review_manifest"]),
        Path(dataset_config["review_decisions"]),
    )
    candidates, excluded = approved_rows(store, config["prompt"])
    kept, duplicates = deduplicate_rows(
        candidates,
        project_root,
        int(dataset_config["duplicate_hamming_distance"]),
    )
    assignment = split_reviewed_rows(
        kept,
        {name: float(value) for name, value in dataset_config["splits"].items()},
        int(dataset_config["split_seed"]),
    )
    splits: dict[str, list[dict[str, Any]]] = {
        name: [] for name in dataset_config["splits"]
    }
    for row in kept:
        row["split"] = assignment[row["session_id"]]
        splits[row["split"]].append(row)

    output_dir = project_root / dataset_config["output"]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "manifest.jsonl", kept)
    for split, split_rows in splits.items():
        write_jsonl(output_dir / f"{split}.jsonl", split_rows)
    write_jsonl(output_dir / "excluded_near_duplicates.jsonl", duplicates)

    session_sets = {
        split: {row["session_id"] for row in split_rows}
        for split, split_rows in splits.items()
    }
    overlap = {
        f"{left}:{right}": sorted(session_sets[left] & session_sets[right])
        for left in splits
        for right in splits
        if left < right
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_summary": store.summary(),
        "approved_eligible_before_dedup": len(candidates),
        "excluded_before_dedup": dict(sorted(excluded.items())),
        "near_duplicates_removed": len(duplicates),
        "dedup_rule": {
            "scope": "same session, capture phase, and exact four-field label",
            "hash": "64-bit difference hash",
            "max_hamming_distance": int(dataset_config["duplicate_hamming_distance"]),
        },
        "final": _distribution(kept),
        "splits": {name: _distribution(rows) for name, rows in splits.items()},
        "session_overlap": overlap,
    }
    (output_dir / "qa_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    config_path = args.config if args.config.is_absolute() else project_root / args.config
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    print(json.dumps(prepare(project_root, config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
