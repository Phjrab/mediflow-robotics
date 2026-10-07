#!/usr/bin/env python3
"""Build a separate 48-episode LeRobot v3 dataset, excluding long idle episodes.

This preserves the source dataset and copies MP4 files byte-for-byte.  The
derived episode metadata points to the original per-file video timestamps.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from build_trimmed_leadin_dataset import NUMERIC_KEYS, numeric_stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--drop-after-s", type=float, default=5.0)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    if source == output or source in output.parents:
        raise ValueError("output must be separate from source")
    manifest = json.loads(args.manifest.read_text())
    if Path(manifest["dataset"]).resolve() != source:
        raise ValueError("manifest does not refer to source dataset")
    excluded = sorted(
        int(row["episode_index"])
        for row in manifest["episodes"]
        if row["first_sustained_body_motion_s"] is not None
        and row["first_sustained_body_motion_s"] > args.drop_after_s
    )
    if len(excluded) != 12:
        raise ValueError(f"expected exactly 12 excluded episodes, got {excluded}")

    info = json.loads((source / "meta/info.json").read_text())
    source_stats = json.loads((source / "meta/stats.json").read_text())
    data_path = next((source / "data").rglob("*.parquet"))
    episode_path = next((source / "meta/episodes").rglob("*.parquet"))
    data = pq.read_table(data_path)
    episode_table = pq.read_table(episode_path)
    episodes = episode_table.to_pylist()
    if len(episodes) != 60 or data.num_rows != info["total_frames"]:
        raise ValueError("source metadata mismatch")

    selected_indices: list[int] = []
    new_episode_ids: list[int] = []
    new_episode_rows: list[dict] = []
    episode_mapping: list[dict] = []
    for old_ep in episodes:
        old_id = int(old_ep["episode_index"])
        if old_id in excluded:
            continue
        new_id = len(new_episode_rows)
        old_start = int(old_ep["dataset_from_index"])
        old_end = int(old_ep["dataset_to_index"])
        length = int(old_ep["length"])
        if old_end - old_start != length:
            raise ValueError(f"invalid source episode {old_id}")
        new_start = len(selected_indices)
        selected_indices.extend(range(old_start, old_end))
        new_episode_ids.extend([new_id] * length)
        new_ep = dict(old_ep)
        new_ep["episode_index"] = new_id
        new_ep["dataset_from_index"] = new_start
        new_ep["dataset_to_index"] = new_start + length
        # The video path and timestamps remain valid because every MP4 is
        # copied unchanged; only episode IDs and data row indices are remapped.
        ep_data = data.slice(old_start, length)
        for feature in NUMERIC_KEYS:
            if feature == "episode_index":
                values = np.full(length, new_id, dtype=np.int64)
            elif feature == "index":
                values = np.arange(new_start, new_start + length, dtype=np.int64)
            else:
                column = ep_data.column(feature)
                values = (
                    np.asarray(column.to_pylist(), dtype=np.float64)
                    if feature in ("action", "observation.state")
                    else column.to_numpy()
                )
            for stat_name, stat_value in numeric_stats(values).items():
                new_ep[f"stats/{feature}/{stat_name}"] = stat_value
        new_episode_rows.append(new_ep)
        episode_mapping.append({"source_episode": old_id, "derived_episode": new_id, "frames": length})

    selected = data.take(pa.array(selected_indices, type=pa.int64()))
    for key, value in {
        "episode_index": pa.array(new_episode_ids, type=pa.int64()),
        "index": pa.array(np.arange(len(selected_indices), dtype=np.int64)),
    }.items():
        selected = selected.set_column(selected.schema.get_field_index(key), key, value)
    new_stats = dict(source_stats)
    for feature in NUMERIC_KEYS:
        column = selected.column(feature)
        values = (
            np.asarray(column.to_pylist(), dtype=np.float64)
            if feature in ("action", "observation.state")
            else column.to_numpy()
        )
        new_stats[feature] = numeric_stats(values)
    new_info = dict(info)
    new_info["total_episodes"] = len(new_episode_rows)
    new_info["total_frames"] = len(selected_indices)
    new_info["splits"] = {"train": f"0:{len(new_episode_rows)}"}

    output.mkdir(parents=True)
    for video in (source / "videos").rglob("*.mp4"):
        destination = output / video.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, destination)
    task_destination = output / "meta/tasks.parquet"
    task_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "meta/tasks.parquet", task_destination)
    new_data_path = output / data_path.relative_to(source)
    new_data_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(selected, new_data_path, compression="zstd")
    new_episode_path = output / episode_path.relative_to(source)
    new_episode_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(new_episode_rows, schema=episode_table.schema), new_episode_path)
    (output / "meta/info.json").write_text(json.dumps(new_info, indent=4) + "\n")
    (output / "meta/stats.json").write_text(json.dumps(new_stats, indent=4) + "\n")
    audit = {
        "source": str(source),
        "output": str(output),
        "repo_id": args.repo_id,
        "drop_after_s": args.drop_after_s,
        "excluded_source_episodes": excluded,
        "mapping": episode_mapping,
        "total_episodes": len(new_episode_rows),
        "total_frames": len(selected_indices),
        "numeric_stats": "recomputed from selected frames",
        "video_stats": "copied from source; approximate for subset",
        "video_files": "copied byte-for-byte; unchanged timestamps",
    }
    (output / "subset_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps({key: audit[key] for key in ("output", "total_episodes", "total_frames", "excluded_source_episodes")}))


if __name__ == "__main__":
    main()
