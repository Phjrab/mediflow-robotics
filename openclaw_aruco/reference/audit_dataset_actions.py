#!/usr/bin/env python3
"""Read A/B/C parquet actions and compare with current follower calibration; no robot I/O."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


def summarize_actions(
    actions: np.ndarray,
    episode_indices: np.ndarray,
    names: list[str],
    body_range_ticks: dict[str, list[int]],
    resolution_ticks: int,
) -> dict[str, Any]:
    if actions.ndim != 2 or actions.shape[0] == 0 or actions.shape[1] != len(names):
        raise ValueError("invalid action matrix")
    if episode_indices.shape != (actions.shape[0],) or not np.isfinite(actions).all():
        raise ValueError("invalid episode indices or non-finite actions")
    if resolution_ticks != 4095 or set(names) != {*body_range_ticks, "gripper.pos"}:
        raise ValueError("motor model or joint names do not match")
    findings = {}
    for index, name in enumerate(names):
        if name == "gripper.pos":
            limit = (0.0, 100.0)
        else:
            low_tick, high_tick = body_range_ticks[name]
            if not 0 <= low_tick < high_tick <= resolution_ticks:
                raise ValueError(f"invalid calibration range: {name}")
            half_width = (high_tick - low_tick) * 180.0 / resolution_ticks
            limit = (-half_width, half_width)
        values = actions[:, index]
        mask = (values < limit[0]) | (values > limit[1])
        findings[name] = {
            "observed_min": round(float(values.min()), 5),
            "observed_max": round(float(values.max()), 5),
            "calibrated_min": round(limit[0], 5),
            "calibrated_max": round(limit[1], 5),
            "exceeding_frames": int(mask.sum()),
            "affected_episodes": int(np.unique(episode_indices[mask]).size),
            "maximum_excess": round(float(max(0, limit[0] - values.min(), values.max() - limit[1])), 5),
        }
    return {
        "frames": int(actions.shape[0]),
        "episodes": int(np.unique(episode_indices).size),
        "joint_findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-config", type=Path, required=True)
    parser.add_argument("--ranges", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if not args.output.parent.is_dir():
        parser.error(f"output parent does not exist: {args.output.parent}")
    source = json.loads(args.dataset_config.read_text(encoding="utf-8"))
    ranges = json.loads(args.ranges.read_text(encoding="utf-8"))
    if source.get("motion_authorized") is not False or ranges.get("motion_authorized") is not False:
        parser.error("all inputs must be motion-disabled")
    if ranges.get("physical_limits_validated") is not False:
        parser.error("calibration ranges must not claim physical validation")
    import pyarrow.parquet as pq

    tasks = {}
    for name, item in source["datasets"].items():
        dataset = Path(item["path"])
        info = json.loads((dataset / "meta/info.json").read_text(encoding="utf-8"))
        joint_names = info["features"]["action"]["names"]
        pieces = sorted((dataset / "data").glob("chunk-*/file-*.parquet"))
        if not pieces:
            raise ValueError(f"no parquet actions: {dataset}")
        action_arrays = []
        episode_arrays = []
        for piece in pieces:
            table = pq.read_table(piece, columns=["action", "episode_index"])
            action_arrays.append(np.asarray(table["action"].to_pylist(), dtype=np.float64))
            episode_arrays.append(np.asarray(table["episode_index"].to_pylist(), dtype=np.int64))
        tasks[name] = summarize_actions(
            np.concatenate(action_arrays),
            np.concatenate(episode_arrays),
            joint_names,
            ranges["body_range_ticks"],
            ranges["resolution_ticks"],
        )
    report = {
        "schema_version": 1,
        "status": "DATASET_ACTION_VS_CURRENT_FOLLOWER_CALIBRATION_ONLY",
        "calibration_source": ranges["source"],
        "tasks": tasks,
        "interpretation": "These are recorded leader actions compared with today's follower calibration, not a claim that past recordings were physically invalid. Degree-mode conversion is not clipped to this range by LeRobot.",
        "motion_authorized": False,
        "robot_commands_sent": 0,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({task: {"frames": data["frames"], "episodes": data["episodes"],
                             "violations": {joint: finding["exceeding_frames"] for joint, finding in data["joint_findings"].items() if finding["exceeding_frames"]}}
                      for task, data in tasks.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
