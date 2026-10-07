from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .dataset import (
    assign_sessions,
    file_sha256,
    load_jsonl,
    validate_source_rows,
    write_jsonl,
)
from .schema import completion_from_source


GRASP_MAP = {"lid_grasp": "lid", "body_grasp": "body"}


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_source_line"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import newly appended Jetson captures without modifying the pilot source."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--staging", type=Path, default=Path("work/imports/jetson_pilot_20260923")
    )
    parser.add_argument("--baseline", type=Path, default=Path("data/pilot/annotations.jsonl"))
    parser.add_argument(
        "--destination", type=Path, default=Path("data/imported/jetson_20260923")
    )
    parser.add_argument(
        "--base-manifest", type=Path, default=Path("dataset_v2/manifest.jsonl")
    )
    parser.add_argument(
        "--review-manifest", type=Path, default=Path("dataset_v2/review_manifest.jsonl")
    )
    parser.add_argument(
        "--config", type=Path, default=Path("vlm_training/config/vlm.yaml")
    )
    return parser


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def main() -> None:
    args = build_parser().parse_args()
    root = args.project_root.resolve()
    staging = _resolve(root, args.staging)
    baseline_path = _resolve(root, args.baseline)
    destination = _resolve(root, args.destination)
    base_manifest_path = _resolve(root, args.base_manifest)
    review_manifest_path = _resolve(root, args.review_manifest)
    config_path = _resolve(root, args.config)

    baseline = load_jsonl(baseline_path)
    staged = load_jsonl(staging / "annotations.jsonl")
    if len(staged) < len(baseline):
        raise SystemExit("staging annotations are shorter than the baseline")
    if [_clean(row) for row in staged[: len(baseline)]] != [_clean(row) for row in baseline]:
        raise SystemExit("staging prefix does not exactly match the immutable baseline")
    incoming = staged[len(baseline) :]
    if not incoming:
        raise SystemExit("no newly appended records found")

    errors = validate_source_rows(incoming, staging)
    if errors:
        raise SystemExit("incoming validation failed:\n" + "\n".join(errors[:20]))

    destination_images = destination / "images"
    destination_videos = destination / "videos"
    destination_images.mkdir(parents=True, exist_ok=True)
    destination_videos.mkdir(parents=True, exist_ok=True)
    imported_annotations: list[dict[str, Any]] = []
    for row in incoming:
        source_image = staging / str(row["image"])
        target_image = destination_images / source_image.name
        if target_image.exists():
            if file_sha256(target_image) != file_sha256(source_image):
                raise SystemExit(f"existing imported image differs: {target_image}")
        else:
            shutil.copy2(source_image, target_image)
        imported_annotations.append(_clean(row))
    write_jsonl(destination / "annotations.jsonl", imported_annotations)

    incoming_trial_ids = {
        str(row["trial_id"]) for row in incoming if row.get("trial_id")
    }
    copied_videos: set[str] = set()
    for row in incoming:
        relative = row.get("video")
        if not relative or relative in copied_videos:
            continue
        source_video = staging / str(relative)
        if not source_video.is_file():
            raise SystemExit(f"missing video referenced by incoming row: {source_video}")
        target_video = destination_videos / source_video.name
        if target_video.exists():
            if file_sha256(target_video) != file_sha256(source_video):
                raise SystemExit(f"existing imported video differs: {target_video}")
        else:
            shutil.copy2(source_video, target_video)
        copied_videos.add(str(relative))
    staged_trials_path = staging / "trials.jsonl"
    if incoming_trial_ids and not staged_trials_path.is_file():
        raise SystemExit("video annotations exist but staging trials.jsonl is missing")
    if staged_trials_path.is_file():
        staged_trials = load_jsonl(staged_trials_path)
        selected_trials = [
            _clean(row) for row in staged_trials if str(row.get("trial_id")) in incoming_trial_ids
        ]
        if len(selected_trials) != len(incoming_trial_ids):
            raise SystemExit("not every incoming trial_id has a trials.jsonl record")
        write_jsonl(destination / "trials.jsonl", selected_trials)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    session_rows = assign_sessions(incoming, float(config["dataset"]["session_gap_seconds"]))
    for row in session_rows:
        if row.get("trial_id"):
            row["session_id"] = f"video_{row['trial_id']}"
    base_rows = load_jsonl(base_manifest_path)
    for row in base_rows:
        row.pop("_source_line", None)
    existing_ids = {row["id"] for row in base_rows}
    imported_rows: list[dict[str, Any]] = []
    for source in session_rows:
        sample_id = Path(str(source["image"])).stem
        if sample_id in existing_ids:
            raise SystemExit(f"incoming id collides with review manifest: {sample_id}")
        answer = source["answer"]
        qa_flags: list[str] = []
        if str(source["camera"]).startswith("http://"):
            qa_flags.append("target_ambiguity_risk")
        if answer.get("capture_phase") == "after_place":
            qa_flags.append("after_place_not_for_grasp_training")
        completion = completion_from_source(answer)
        if completion["orientation"] in {"tilted", "unknown"}:
            qa_flags.append("underrepresented_orientation")
        imported_rows.append(
            {
                "id": sample_id,
                "image": (destination_images / f"{sample_id}.jpg")
                .relative_to(root)
                .as_posix(),
                "captured_at": source["captured_at"],
                "camera": source["camera"],
                "session_id": f"jetson_20260923_{source['session_id']}",
                "split": "incoming",
                "prompt": config["prompt"],
                "completion": completion,
                "proposed_grasp_region": GRASP_MAP.get(answer.get("grasp_strategy")),
                "review_status": "pending",
                "qa_flags": qa_flags,
                "near_duplicate_of": None,
                "source": {
                    "dataset": "data/imported/jetson_20260923",
                    "line": source["_source_line"],
                    "capture_phase": answer.get("capture_phase"),
                    "action_result": answer.get("action_result"),
                    "grasp_strategy": answer.get("grasp_strategy"),
                    "needs_approval": answer.get("needs_approval"),
                    "trial_id": source.get("trial_id"),
                    "video": (
                        (destination_videos / Path(str(source["video"])).name)
                        .relative_to(root)
                        .as_posix()
                        if source.get("video")
                        else None
                    ),
                    "video_offset_seconds": source.get("video_offset_seconds"),
                    "frame_selection": source.get("frame_selection"),
                },
            }
        )

    combined = base_rows + imported_rows
    if len({row["id"] for row in combined}) != len(combined):
        raise SystemExit("combined review manifest contains duplicate ids")
    write_jsonl(review_manifest_path, combined)
    report = {
        "baseline_records": len(baseline),
        "staged_records": len(staged),
        "incoming_records": len(incoming),
        "review_manifest_records": len(combined),
        "incoming_ids": [imported_rows[0]["id"], imported_rows[-1]["id"]],
        "classes": dict(Counter(row["completion"]["medicine_id"] for row in imported_rows)),
        "orientations": dict(
            Counter(row["completion"]["orientation"] for row in imported_rows)
        ),
        "phases": dict(Counter(row["source"]["capture_phase"] for row in imported_rows)),
        "grasp_regions": dict(
            Counter(row["proposed_grasp_region"] for row in imported_rows)
        ),
        "video_trials": len(incoming_trial_ids),
        "videos_copied": len(copied_videos),
    }
    report_path = review_manifest_path.parent / "capture_import_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
