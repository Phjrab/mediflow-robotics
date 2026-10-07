from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from .schema import completion_from_source


REQUIRED_TOP_LEVEL = {
    "image",
    "captured_at",
    "camera",
    "width",
    "height",
    "instruction",
    "answer",
}


@dataclass(frozen=True)
class PreparedData:
    rows: list[dict[str, Any]]
    report: dict[str, Any]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"record at {path}:{line_number} is not an object")
            row["_source_line"] = line_number
            rows.append(row)
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source_rows(rows: Iterable[dict[str, Any]], dataset_root: Path) -> list[str]:
    errors: list[str] = []
    root = dataset_root.resolve()
    for row in rows:
        line = row.get("_source_line", "?")
        missing = REQUIRED_TOP_LEVEL - set(row)
        if missing:
            errors.append(f"line {line}: missing keys {sorted(missing)}")
            continue
        image_rel = Path(str(row["image"]))
        if image_rel.is_absolute() or ".." in image_rel.parts:
            errors.append(f"line {line}: unsafe image path {image_rel}")
            continue
        image_path = (root / image_rel).resolve()
        if root not in image_path.parents:
            errors.append(f"line {line}: image escapes dataset root")
            continue
        if not image_path.is_file():
            errors.append(f"line {line}: missing image {image_rel}")
            continue
        try:
            with Image.open(image_path) as image:
                image.load()
                if image.size != (row["width"], row["height"]):
                    errors.append(
                        f"line {line}: declared dimensions {(row['width'], row['height'])} "
                        f"do not match {image.size}"
                    )
        except Exception as exc:  # Pillow raises several format-specific exceptions.
            errors.append(f"line {line}: cannot decode {image_rel}: {exc}")
    return errors


def assign_sessions(rows: list[dict[str, Any]], gap_seconds: float) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["captured_at"])
    output: list[dict[str, Any]] = []
    session_number = 0
    previous_time: datetime | None = None
    previous_camera: str | None = None
    for source in ordered:
        timestamp = datetime.fromisoformat(source["captured_at"])
        new_session = (
            previous_time is None
            or (timestamp - previous_time).total_seconds() > gap_seconds
            or source["camera"] != previous_camera
        )
        if new_session:
            session_number += 1
        row = dict(source)
        row["session_id"] = f"session_{session_number:03d}"
        output.append(row)
        previous_time = timestamp
        previous_camera = source["camera"]
    return output


def _feature_counts(rows: Iterable[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        answer = row["answer"]
        counts["total"] += 1
        counts[f"class:{answer.get('target_class')}"] += 1
        counts[f"orientation:{answer.get('object_state')}"] += 1
        counts[f"camera:{row.get('camera')}"] += 1
    return counts


def split_grouped_rows(
    rows: list[dict[str, Any]],
    fractions: dict[str, float],
    seed: int,
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

    global_counts = _feature_counts(rows)
    split_counts = {name: Counter() for name in fractions}
    assignment: dict[str, str] = {}

    def global_cost(candidate_split: str, candidate_counts: Counter[str]) -> float:
        total = 0.0
        for split_name, fraction in fractions.items():
            counts = split_counts[split_name].copy()
            if split_name == candidate_split:
                counts.update(candidate_counts)
            for feature, global_value in global_counts.items():
                target = max(global_value * fraction, 1.0)
                weight = 2.0 if feature == "total" else 1.0
                total += weight * ((counts[feature] - target) / target) ** 2
        return total

    for session_id, group_rows in groups:
        counts = _feature_counts(group_rows)
        candidates = list(fractions)
        rng.shuffle(candidates)
        selected = min(candidates, key=lambda name: global_cost(name, counts))
        assignment[session_id] = selected
        split_counts[selected].update(counts)

    # A pathological tiny dataset should fail visibly instead of silently producing no eval set.
    empty = [name for name, counts in split_counts.items() if not counts["total"]]
    if empty:
        raise ValueError(f"grouped split produced empty partitions: {empty}")
    return assignment


def _dhash(path: Path, size: int = 8) -> int:
    with Image.open(path) as image:
        gray = image.convert("L").resize((size + 1, size))
        pixels = list(gray.getdata())
    value = 0
    for y in range(size):
        for x in range(size):
            left = pixels[y * (size + 1) + x]
            right = pixels[y * (size + 1) + x + 1]
            value = (value << 1) | int(left > right)
    return value


def prepare_rows(
    source_rows: list[dict[str, Any]],
    dataset_root: Path,
    project_root: Path,
    prompt: str,
    phase: str,
    gap_seconds: float,
    fractions: dict[str, float],
    seed: int,
) -> PreparedData:
    session_rows = assign_sessions(source_rows, gap_seconds)
    candidates = [
        row for row in session_rows if row.get("answer", {}).get("capture_phase") == phase
    ]
    assignment = split_grouped_rows(candidates, fractions, seed)

    prepared: list[dict[str, Any]] = []
    recent_hashes: dict[str, list[tuple[str, int]]] = defaultdict(list)
    duplicate_candidates = 0
    for row in candidates:
        source_path = dataset_root / row["image"]
        image_path = source_path.resolve().relative_to(project_root.resolve())
        image_hash = _dhash(source_path)
        near_duplicate_of: str | None = None
        for candidate_id, candidate_hash in recent_hashes[row["session_id"]][-5:]:
            if (image_hash ^ candidate_hash).bit_count() <= 2:
                near_duplicate_of = candidate_id
                duplicate_candidates += 1
                break

        sample_id = Path(row["image"]).stem
        recent_hashes[row["session_id"]].append((sample_id, image_hash))
        completion = completion_from_source(row["answer"])
        qa_flags: list[str] = []
        if near_duplicate_of:
            qa_flags.append("near_duplicate_candidate")
        if str(row["camera"]).startswith("http://"):
            qa_flags.append("target_ambiguity_risk")
        if completion["orientation"] in {"tilted", "unknown"}:
            qa_flags.append("underrepresented_orientation")

        prepared.append(
            {
                "id": sample_id,
                "image": image_path.as_posix(),
                "captured_at": row["captured_at"],
                "camera": row["camera"],
                "session_id": row["session_id"],
                "split": assignment[row["session_id"]],
                "prompt": prompt,
                "completion": completion,
                "review_status": "pending",
                "qa_flags": qa_flags,
                "near_duplicate_of": near_duplicate_of,
                "source": {
                    "line": row["_source_line"],
                    "capture_phase": row["answer"]["capture_phase"],
                    "action_result": row["answer"]["action_result"],
                },
            }
        )

    report = {
        "source_records": len(source_rows),
        "candidate_records": len(candidates),
        "phase": phase,
        "session_count_all": len({row["session_id"] for row in session_rows}),
        "session_count_candidates": len({row["session_id"] for row in candidates}),
        "split_records": dict(Counter(row["split"] for row in prepared)),
        "split_sessions": {
            split: len({row["session_id"] for row in prepared if row["split"] == split})
            for split in fractions
        },
        "medicine_counts": dict(Counter(row["completion"]["medicine_id"] for row in prepared)),
        "orientation_counts": dict(
            Counter(row["completion"]["orientation"] for row in prepared)
        ),
        "camera_counts": dict(Counter(row["camera"] for row in prepared)),
        "near_duplicate_candidates": duplicate_candidates,
        "review_status": dict(Counter(row["review_status"] for row in prepared)),
        "warnings": [
            "All prepared labels remain pending manual review.",
            "Ceiling-camera frames may contain multiple containers without a target bbox.",
            "Near-duplicate candidates are kept but are grouped by session into one split.",
            "graspable, required_action, confidence, and reason are intentionally not trained.",
        ],
    }
    return PreparedData(prepared, report)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp_path.replace(path)

