#!/usr/bin/env python3
"""Check the separate 48-episode subset against every retained source row."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch

from lerobot.datasets.lerobot_dataset import LeRobotDataset


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    source, derived = args.source.resolve(), args.derived.resolve()
    audit = json.loads((derived / "subset_audit.json").read_text())
    source_info = json.loads((source / "meta/info.json").read_text())
    derived_info = json.loads((derived / "meta/info.json").read_text())
    assert source_info["features"] == derived_info["features"]
    assert source_info["fps"] == derived_info["fps"] == 30
    assert len(audit["excluded_source_episodes"]) == 12
    assert derived_info["total_episodes"] == audit["total_episodes"] == 48
    assert derived_info["total_frames"] == audit["total_frames"]

    source_data = pq.read_table(next((source / "data").rglob("*.parquet")))
    derived_data = pq.read_table(next((derived / "data").rglob("*.parquet")))
    source_episodes = pq.read_table(next((source / "meta/episodes").rglob("*.parquet"))).to_pylist()
    derived_episodes = pq.read_table(next((derived / "meta/episodes").rglob("*.parquet"))).to_pylist()
    assert len(source_episodes) == 60
    assert len(derived_episodes) == 48
    assert derived_data.num_rows == audit["total_frames"]

    for mapping in audit["mapping"]:
        old_id, new_id = mapping["source_episode"], mapping["derived_episode"]
        original, edited = source_episodes[old_id], derived_episodes[new_id]
        assert original["episode_index"] == old_id
        assert edited["episode_index"] == new_id
        old_start, new_start = original["dataset_from_index"], edited["dataset_from_index"]
        length = original["length"]
        assert length == edited["length"] == mapping["frames"]
        assert edited["dataset_to_index"] - new_start == length
        for key in ("action", "observation.state", "frame_index", "timestamp", "task_index"):
            assert source_data.column(key).slice(old_start, length).to_pylist() == derived_data.column(key).slice(new_start, length).to_pylist(), (old_id, key)
        assert derived_data.column("episode_index").slice(new_start, length).to_pylist() == [new_id] * length
        assert derived_data.column("index").slice(new_start, length).to_pylist() == list(range(new_start, new_start + length))
        for video_key in (key for key, feature in source_info["features"].items() if feature["dtype"] == "video"):
            for suffix in ("from_timestamp", "to_timestamp"):
                column = f"videos/{video_key}/{suffix}"
                assert np.isclose(edited[column], original[column], atol=1e-6), (old_id, column)

    videos = sorted((source / "videos").rglob("*.mp4"))
    for video in videos:
        assert sha256(video) == sha256(derived / video.relative_to(source)), video

    source_dataset = LeRobotDataset(
        repo_id="Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846",
        root=source,
        video_backend="pyav",
    )
    derived_dataset = LeRobotDataset(repo_id=args.repo_id, root=derived, video_backend="pyav")
    assert len(derived_dataset) == audit["total_frames"]
    checked_images = 0
    sample_new_ids = sorted({0, 1, 8, 9, 18, 28, 38, 47})
    for new_id in sample_new_ids:
        mapping = audit["mapping"][new_id]
        old_id = mapping["source_episode"]
        old_start = source_episodes[old_id]["dataset_from_index"]
        new_start = derived_episodes[new_id]["dataset_from_index"]
        length = mapping["frames"]
        for frame in sorted({0, length // 2, length - 1}):
            old_row, new_row = source_dataset[old_start + frame], derived_dataset[new_start + frame]
            for key in ("action", "observation.state"):
                assert torch.equal(old_row[key], new_row[key]), (old_id, frame, key)
            for video_key in source_dataset.meta.video_keys:
                assert torch.equal(old_row[video_key], new_row[video_key]), (old_id, frame, video_key)
                checked_images += 1

    report = {
        "source": str(source),
        "derived": str(derived),
        "source_episodes": 60,
        "retained_episodes": 48,
        "excluded_source_episodes": audit["excluded_source_episodes"],
        "retained_frames": audit["total_frames"],
        "numeric_rows_verified": audit["total_frames"],
        "video_files_sha256_verified": len(videos),
        "decoded_images_equal": checked_images,
        "status": "passed",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
