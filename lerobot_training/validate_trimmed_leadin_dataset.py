#!/usr/bin/env python3
"""Validate retained rows, video offsets, and source-byte preservation."""

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
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    derived = args.derived.resolve()
    audit = json.loads((derived / "trim_audit.json").read_text())
    source_info = json.loads((source / "meta/info.json").read_text())
    derived_info = json.loads((derived / "meta/info.json").read_text())
    assert source_info["features"] == derived_info["features"]
    assert source_info["fps"] == derived_info["fps"] == 30
    assert derived_info["total_episodes"] == source_info["total_episodes"] == 60
    assert derived_info["total_frames"] == audit["retained_frames"]

    source_data = pq.read_table(next((source / "data").rglob("*.parquet")))
    derived_data = pq.read_table(next((derived / "data").rglob("*.parquet")))
    source_episodes = pq.read_table(next((source / "meta/episodes").rglob("*.parquet"))).to_pylist()
    derived_episodes = pq.read_table(next((derived / "meta/episodes").rglob("*.parquet"))).to_pylist()
    assert len(source_episodes) == len(derived_episodes) == 60

    for cut, original, edited in zip(audit["cuts"], source_episodes, derived_episodes, strict=True):
        ep_id = cut["episode_index"]
        assert original["episode_index"] == edited["episode_index"] == ep_id
        old_start = original["dataset_from_index"] + cut["cut_frames"]
        new_start = edited["dataset_from_index"]
        length = edited["length"]
        assert length == cut["retained_frames"]
        assert edited["dataset_to_index"] - new_start == length
        for key in ("action", "observation.state", "episode_index", "task_index"):
            old_col = source_data.column(key).slice(old_start, length).to_pylist()
            new_col = derived_data.column(key).slice(new_start, length).to_pylist()
            assert old_col == new_col, (ep_id, key)
        assert derived_data.column("frame_index").slice(new_start, length).to_pylist() == list(range(length))
        timestamps = derived_data.column("timestamp").slice(new_start, length).to_numpy()
        assert np.allclose(timestamps, np.arange(length) / 30, atol=1e-4)
        for video_key in (key for key, feature in source_info["features"].items() if feature["dtype"] == "video"):
            prefix = f"videos/{video_key}"
            assert np.isclose(
                edited[f"{prefix}/from_timestamp"],
                original[f"{prefix}/from_timestamp"] + cut["cut_frames"] / 30,
                atol=1e-6,
            )
            assert np.isclose(
                edited[f"{prefix}/to_timestamp"], original[f"{prefix}/to_timestamp"], atol=1e-6
            )

    video_paths = sorted((source / "videos").rglob("*.mp4"))
    for video in video_paths:
        assert sha256(video) == sha256(derived / video.relative_to(source)), video

    source_dataset = LeRobotDataset(
        repo_id="Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846",
        root=source,
        video_backend="pyav",
    )
    derived_dataset = LeRobotDataset(repo_id=args.repo_id, root=derived, video_backend="pyav")
    assert len(derived_dataset) == audit["retained_frames"]
    checked_images = 0
    video_episode_ids = [0, 1, 9, 10, 20, 40, 50, 59]
    for ep_id in video_episode_ids:
        cut = audit["cuts"][ep_id]
        old_start = source_episodes[ep_id]["dataset_from_index"] + cut["cut_frames"]
        new_start = derived_episodes[ep_id]["dataset_from_index"]
        length = cut["retained_frames"]
        for frame in sorted({0, length // 2, length - 1}):
            original = source_dataset[old_start + frame]
            edited = derived_dataset[new_start + frame]
            for key in ("action", "observation.state"):
                assert torch.equal(original[key], edited[key]), (ep_id, frame, key)
            for video_key in source_dataset.meta.video_keys:
                assert torch.equal(original[video_key], edited[video_key]), (ep_id, frame, video_key)
                checked_images += 1

    report = {
        "source": str(source),
        "derived": str(derived),
        "episodes": 60,
        "source_frames": audit["source_frames"],
        "retained_frames": audit["retained_frames"],
        "removed_frames": audit["removed_frames"],
        "numeric_rows_verified": audit["retained_frames"],
        "video_files_sha256_verified": len(video_paths),
        "decoded_images_equal": checked_images,
        "decoded_episode_ids": video_episode_ids,
        "status": "passed",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
