#!/usr/bin/env python3
"""Build a separate LeRobot v3 dataset with shortened episode lead-ins.

The existing MP4 files are copied without transcoding.  A retained frame at
new episode timestamp t is read from the original video at old_from + cut/fps
+ t.  Parquet rows and episode metadata are reindexed to match.  The source
dataset is never modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


NUMERIC_KEYS = (
    "action",
    "observation.state",
    "timestamp",
    "frame_index",
    "episode_index",
    "index",
    "task_index",
)
QUANTILES = (1, 10, 50, 90, 99)


def numeric_stats(values: np.ndarray) -> dict[str, list]:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if len(values) == 0 or not np.isfinite(values).all():
        raise ValueError("empty or non-finite numeric feature")
    stats = {
        "min": values.min(axis=0).tolist(),
        "max": values.max(axis=0).tolist(),
        "mean": values.mean(axis=0).tolist(),
        "std": values.std(axis=0).tolist(),
        "count": [len(values)],
    }
    for q in QUANTILES:
        stats[f"q{q:02d}"] = np.percentile(values, q, axis=0).tolist()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    if source == output or source in output.parents:
        raise ValueError("output must be separate from source")

    info = json.loads((source / "meta/info.json").read_text())
    original_stats = json.loads((source / "meta/stats.json").read_text())
    manifest = json.loads(args.manifest.read_text())
    if Path(manifest["dataset"]).resolve() != source:
        raise ValueError("manifest does not refer to source dataset")
    if info["codebase_version"] != "v3.0":
        raise ValueError("expected a LeRobot v3.0 dataset")
    fps = int(info["fps"])

    data_paths = sorted((source / "data").rglob("*.parquet"))
    episode_paths = sorted((source / "meta/episodes").rglob("*.parquet"))
    if len(data_paths) != 1 or len(episode_paths) != 1:
        raise ValueError("this builder expects one data and one episode parquet file")
    data_path = data_paths[0]
    episode_path = episode_paths[0]
    data = pq.read_table(data_path)
    episode_table = pq.read_table(episode_path)
    episodes = episode_table.to_pylist()
    cuts = {int(row["episode_index"]): int(row["candidate_trim_frames"]) for row in manifest["episodes"]}
    if len(episodes) != info["total_episodes"] or len(cuts) != len(episodes):
        raise ValueError("episode count mismatch")
    if data.num_rows != info["total_frames"]:
        raise ValueError("frame count mismatch")

    source_ep_ids = data.column("episode_index").to_numpy()
    source_frame_ids = data.column("frame_index").to_numpy()
    source_timestamps = data.column("timestamp").to_numpy()
    keep_indices: list[int] = []
    new_frame_ids: list[int] = []
    new_timestamps: list[float] = []
    output_episode_rows: list[dict] = []
    cut_audit: list[dict] = []

    for ep in episodes:
        ep_id = int(ep["episode_index"])
        old_start = int(ep["dataset_from_index"])
        old_end = int(ep["dataset_to_index"])
        old_length = int(ep["length"])
        cut = cuts[ep_id]
        if old_end - old_start != old_length or not 0 <= cut < old_length:
            raise ValueError(f"invalid length/cut for episode {ep_id}")
        if not np.all(source_ep_ids[old_start:old_end] == ep_id):
            raise ValueError(f"episode index mismatch for episode {ep_id}")
        if not np.array_equal(source_frame_ids[old_start:old_end], np.arange(old_length)):
            raise ValueError(f"frame index mismatch for episode {ep_id}")
        if not np.allclose(source_timestamps[old_start:old_end], np.arange(old_length) / fps, atol=1e-4):
            raise ValueError(f"timestamp mismatch for episode {ep_id}")

        new_start = len(keep_indices)
        kept_source_indices = list(range(old_start + cut, old_end))
        keep_indices.extend(kept_source_indices)
        new_length = len(kept_source_indices)
        new_frame_ids.extend(range(new_length))
        new_timestamps.extend(np.arange(new_length, dtype=np.float32) / fps)

        new_ep = dict(ep)
        new_ep["length"] = new_length
        new_ep["dataset_from_index"] = new_start
        new_ep["dataset_to_index"] = new_start + new_length
        for video_key in (key for key, feature in info["features"].items() if feature["dtype"] == "video"):
            prefix = f"videos/{video_key}"
            original_from = float(ep[f"{prefix}/from_timestamp"])
            original_to = float(ep[f"{prefix}/to_timestamp"])
            new_from = original_from + cut / fps
            new_to = new_from + new_length / fps
            if not np.isclose(new_to, original_to, atol=1e-4):
                raise ValueError(f"video boundary mismatch for {ep_id}/{video_key}")
            new_ep[f"{prefix}/from_timestamp"] = new_from
            new_ep[f"{prefix}/to_timestamp"] = new_to

        # The original video statistics are retained.  They were sampled from
        # the same camera files but include removed lead-in frames; see audit.
        ep_data = data.take(pa.array(kept_source_indices, type=pa.int64()))
        for feature in NUMERIC_KEYS:
            if feature == "frame_index":
                values = np.arange(new_length, dtype=np.int64)
            elif feature == "timestamp":
                values = np.arange(new_length, dtype=np.float32) / fps
            elif feature == "index":
                values = np.arange(new_start, new_start + new_length, dtype=np.int64)
            else:
                column = ep_data.column(feature)
                values = (
                    np.asarray(column.to_pylist(), dtype=np.float64)
                    if feature in ("action", "observation.state")
                    else column.to_numpy()
                )
            for stat_name, stat_value in numeric_stats(values).items():
                new_ep[f"stats/{feature}/{stat_name}"] = stat_value
        output_episode_rows.append(new_ep)
        cut_audit.append({
            "episode_index": ep_id,
            "source_frames": old_length,
            "cut_frames": cut,
            "retained_frames": new_length,
            "source_first_retained_index": old_start + cut,
        })

    trimmed = data.take(pa.array(keep_indices, type=pa.int64()))
    replacements = {
        "index": pa.array(np.arange(len(keep_indices), dtype=np.int64)),
        "frame_index": pa.array(new_frame_ids, type=pa.int64()),
        "timestamp": pa.array(new_timestamps, type=pa.float32()),
    }
    for key, replacement in replacements.items():
        trimmed = trimmed.set_column(trimmed.schema.get_field_index(key), key, replacement)

    new_stats = dict(original_stats)
    for feature in NUMERIC_KEYS:
        column = trimmed.column(feature)
        values = (
            np.asarray(column.to_pylist(), dtype=np.float64)
            if feature in ("action", "observation.state")
            else column.to_numpy()
        )
        new_stats[feature] = numeric_stats(values)
    new_info = dict(info)
    new_info["total_frames"] = len(keep_indices)

    # Create the new tree only after every read-only consistency check passes.
    output.mkdir(parents=True)
    try:
        for video in (source / "videos").rglob("*.mp4"):
            destination = output / video.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(video, destination)
        task_source = source / "meta/tasks.parquet"
        task_destination = output / "meta/tasks.parquet"
        task_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(task_source, task_destination)

        destination_data = output / data_path.relative_to(source)
        destination_data.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(trimmed, destination_data, compression="zstd")
        destination_episodes = output / episode_path.relative_to(source)
        destination_episodes.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(output_episode_rows, schema=episode_table.schema), destination_episodes)
        (output / "meta/info.json").write_text(json.dumps(new_info, indent=4) + "\n")
        (output / "meta/stats.json").write_text(json.dumps(new_stats, indent=4) + "\n")

        audit = {
            "source": str(source),
            "output": str(output),
            "repo_id": args.repo_id,
            "fps": fps,
            "episodes": len(episodes),
            "source_frames": data.num_rows,
            "retained_frames": len(keep_indices),
            "removed_frames": data.num_rows - len(keep_indices),
            "cut_rule": manifest["rule"],
            "video_data": "copied without transcoding; episode start offsets adjusted",
            "video_stats": "copied from source; includes original lead-in samples",
            "numeric_stats": "recomputed from retained frames",
            "cuts": cut_audit,
        }
        (output / "trim_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    except Exception:
        # Preserve partial output for inspection; never touch the source.
        raise
    print(json.dumps({key: audit[key] for key in ("output", "episodes", "source_frames", "retained_frames", "removed_frames")}))


if __name__ == "__main__":
    main()
