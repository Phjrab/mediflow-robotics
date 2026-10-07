#!/usr/bin/env python3
"""Read-only LeRobot episode lead-in analysis; proposes trims without changing data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


def first_sustained_motion(action: np.ndarray, threshold_deg: float, frames: int) -> int | None:
    """First run of `frames` with any body joint > threshold from its initial target."""
    reference = np.median(action[: min(5, len(action)), :5], axis=0)
    moved = np.max(np.abs(action[:, :5] - reference), axis=1) > threshold_deg
    if len(moved) < frames:
        return None
    hits = np.convolve(moved.astype(np.int8), np.ones(frames, dtype=np.int8), mode="valid")
    indices = np.flatnonzero(hits == frames)
    return int(indices[0]) if len(indices) else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--threshold-deg", type=float, default=3.0)
    parser.add_argument("--sustained-frames", type=int, default=3)
    parser.add_argument("--retain-before-s", type=float, default=0.5)
    args = parser.parse_args()

    files = sorted((args.dataset / "data").rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet data under {args.dataset}")
    table = pa.concat_tables([pq.read_table(path, columns=["episode_index", "action"]) for path in files])
    episode_ids = table.column("episode_index").to_numpy()
    actions = np.asarray(table.column("action").to_pylist(), dtype=np.float32)
    keep_before = round(args.retain_before_s * args.fps)

    episodes = []
    for episode_id in np.unique(episode_ids):
        episode_action = actions[episode_ids == episode_id]
        onset = first_sustained_motion(episode_action, args.threshold_deg, args.sustained_frames)
        cut = max(0, onset - keep_before) if onset is not None else 0
        episodes.append({
            "episode_index": int(episode_id),
            "frames": len(episode_action),
            "first_sustained_body_motion_frame": onset,
            "first_sustained_body_motion_s": round(onset / args.fps, 3) if onset is not None else None,
            "candidate_trim_frames": cut,
            "candidate_trim_s": round(cut / args.fps, 3),
        })

    starts = np.array([e["first_sustained_body_motion_s"] for e in episodes if e["first_sustained_body_motion_s"] is not None])
    total_frames = sum(e["frames"] for e in episodes)
    trim_frames = sum(e["candidate_trim_frames"] for e in episodes)
    report = {
        "dataset": str(args.dataset),
        "read_only": True,
        "rule": {
            "threshold_deg": args.threshold_deg,
            "sustained_frames": args.sustained_frames,
            "retain_before_s": args.retain_before_s,
            "body_joints": "first five action channels; gripper excluded",
        },
        "summary": {
            "episodes": len(episodes),
            "episodes_without_detected_motion": len(episodes) - len(starts),
            "first_motion_median_s": round(float(np.median(starts)), 3) if len(starts) else None,
            "first_motion_p90_s": round(float(np.percentile(starts, 90)), 3) if len(starts) else None,
            "first_motion_max_s": round(float(np.max(starts)), 3) if len(starts) else None,
            "episodes_first_motion_after_5_s": int(np.sum(starts > 5)),
            "episodes_first_motion_after_8_s": int(np.sum(starts > 8)),
            "total_frames": total_frames,
            "candidate_trim_frames": trim_frames,
            "candidate_trim_fraction": round(trim_frames / total_frames, 4),
        },
        "episodes": episodes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
