"""Build non-executable OpenClaw references from recorded LeRobot data.

This module only reads dataset metadata and parquet files.  Its output is a
statistical reference for review; it is deliberately not a robot command or
an execution authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


REFERENCE_POINTS = 101
MIN_REFERENCE_DURATION_SECONDS = 5.0
MIN_BODY_MOTION_RANGE_SUM = 100.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resample_trajectory(values: np.ndarray, points: int = REFERENCE_POINTS) -> np.ndarray:
    """Linearly resample a joint trajectory on normalized episode time."""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] < 2 or array.shape[1] < 1:
        raise ValueError("trajectory must contain at least two rows and one joint")
    old_time = np.linspace(0.0, 1.0, array.shape[0])
    new_time = np.linspace(0.0, 1.0, points)
    return np.column_stack([np.interp(new_time, old_time, array[:, joint]) for joint in range(array.shape[1])])


def _rounded(value: np.ndarray | list[float] | float, digits: int = 4) -> Any:
    array = np.asarray(value, dtype=np.float64)
    rounded = np.round(array, digits)
    if rounded.ndim == 0:
        return float(rounded)
    return rounded.tolist()


def build_dataset_reference(
    dataset_dir: Path,
    task: str,
    expected_identity: str,
    identity_verification: str,
) -> dict[str, Any]:
    """Summarize one 60-episode dataset as a fail-closed motion reference."""

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError("pyarrow is required to read the LeRobot dataset") from exc

    info_path = dataset_dir / "meta" / "info.json"
    episodes_path = dataset_dir / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    if not info_path.is_file() or not episodes_path.is_file():
        raise FileNotFoundError(f"incomplete LeRobot dataset: {dataset_dir}")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    data_files = sorted((dataset_dir / "data").glob("chunk-*/*.parquet"))
    if not data_files:
        raise FileNotFoundError(f"no action parquet files in {dataset_dir}")
    data = pa.concat_tables([
        pq.read_table(path, columns=["action", "observation.state", "episode_index"])
        for path in data_files
    ])
    actions = np.asarray(data.column("action").to_pylist(), dtype=np.float64)
    states = np.asarray(data.column("observation.state").to_pylist(), dtype=np.float64)
    episode_ids = np.asarray(data.column("episode_index").to_numpy(), dtype=np.int64)
    episodes = pq.read_table(episodes_path).to_pylist()

    joint_names = info.get("features", {}).get("action", {}).get("names")
    if not isinstance(joint_names, list) or len(joint_names) != actions.shape[1]:
        joint_names = [f"joint_{index}" for index in range(actions.shape[1])]

    trajectories = []
    lengths = []
    durations = []
    episode_numbers = []
    start_states = []
    end_states = []
    rejected_episodes = []
    for row in episodes:
        episode_index = int(row["episode_index"])
        trajectory = actions[episode_ids == episode_index]
        if len(trajectory) < 2:
            rejected_episodes.append({"episode_index": episode_index, "reasons": ["fewer than two action frames"]})
            continue
        duration = float(len(trajectory)) / float(info["fps"])
        body_motion_range_sum = float(np.ptp(trajectory[:, : min(5, trajectory.shape[1])], axis=0).sum())
        rejection_reasons = []
        if duration < MIN_REFERENCE_DURATION_SECONDS:
            rejection_reasons.append(f"duration {duration:.4f}s is below {MIN_REFERENCE_DURATION_SECONDS:.1f}s")
        if body_motion_range_sum < MIN_BODY_MOTION_RANGE_SUM:
            rejection_reasons.append(
                f"body joint range sum {body_motion_range_sum:.4f} is below {MIN_BODY_MOTION_RANGE_SUM:.1f}"
            )
        if rejection_reasons:
            rejected_episodes.append({"episode_index": episode_index, "reasons": rejection_reasons})
            continue
        trajectories.append(resample_trajectory(trajectory))
        state_trajectory = states[episode_ids == episode_index]
        lengths.append(int(len(trajectory)))
        durations.append(duration)
        episode_numbers.append(episode_index)
        start_states.append(state_trajectory[0])
        end_states.append(state_trajectory[-1])
    if not trajectories:
        raise ValueError(f"dataset contains no usable episodes: {dataset_dir}")

    stack = np.stack(trajectories)
    median = np.median(stack, axis=0)
    q10 = np.quantile(stack, 0.10, axis=0)
    q90 = np.quantile(stack, 0.90, axis=0)
    scale = np.maximum(np.quantile(actions, 0.99, axis=0) - np.quantile(actions, 0.01, axis=0), 1.0)
    distances = np.mean(((stack - median) / scale) ** 2, axis=(1, 2))
    representative_offset = int(np.argmin(distances))

    camera_features = sorted(
        name for name in info.get("features", {}) if name.startswith("observation.images.")
    )
    task_descriptions = sorted({item for row in episodes for item in row.get("tasks", [])})
    parquet_hashes = {str(path.relative_to(dataset_dir)): _sha256(path) for path in data_files}

    return {
        "task": task,
        "expected_identity": expected_identity,
        "status": "REFERENCE_ONLY_READY",
        "motion_authorized": False,
        "identity_verification": identity_verification,
        "dataset": {
            "path": str(dataset_dir),
            "info_sha256": _sha256(info_path),
            "action_parquet_sha256": parquet_hashes,
            "episodes": int(info.get("total_episodes", len(episodes))),
            "usable_reference_episodes": len(trajectories),
            "quality_rejected_episodes": rejected_episodes,
            "frames": int(info.get("total_frames", len(actions))),
            "fps": int(info["fps"]),
            "camera_features": camera_features,
            "task_descriptions": task_descriptions,
        },
        "joints": joint_names,
        "episode_duration_seconds": {
            "min": _rounded(min(durations)),
            "median": _rounded(np.median(durations)),
            "max": _rounded(max(durations)),
        },
        "episode_length_frames": {
            "min": min(lengths),
            "median": int(round(float(np.median(lengths)))),
            "max": max(lengths),
        },
        "action_envelope_deg_or_gripper_units": {
            "q01": _rounded(np.quantile(actions, 0.01, axis=0)),
            "q99": _rounded(np.quantile(actions, 0.99, axis=0)),
        },
        "reference": {
            "normalized_time_points": REFERENCE_POINTS,
            "representative_episode": episode_numbers[representative_offset],
            "representative_start_pose": _rounded(stack[representative_offset, 0]),
            "representative_end_pose": _rounded(stack[representative_offset, -1]),
            "representative_start_observation_pose": _rounded(start_states[representative_offset]),
            "representative_end_observation_pose": _rounded(end_states[representative_offset]),
            "start_pose_median": _rounded(median[0]),
            "end_pose_median": _rounded(median[-1]),
            "median_trajectory": _rounded(median),
            "q10_trajectory": _rounded(q10),
            "q90_trajectory": _rounded(q90),
        },
        "blocking_reasons": [
            "recorded trajectories are references only and must not be replayed without live scene and start-pose validation",
            "OpenClaw execution remains disabled until reviewed safety gates and a stop path are implemented",
        ],
    }


def build_reference_library(config: dict[str, Any], root: Path) -> dict[str, Any]:
    if config.get("usage") != "reference_only" or config.get("motion_authorized") is not False:
        raise ValueError("dataset config must be reference-only and motion-disabled")
    datasets = config.get("datasets")
    if not isinstance(datasets, dict) or set(datasets) != {"A", "B", "C"}:
        raise ValueError("dataset config must define exactly A, B, and C")
    references = {}
    for task in ("A", "B", "C"):
        item = datasets[task]
        verification = item.get("identity_verification")
        if not isinstance(verification, str) or not verification.startswith("verified_by_user_"):
            raise ValueError(f"task {task} identity must be verified by the user")
        path = Path(item["path"])
        if not path.is_absolute():
            path = root / path
        references[task] = build_dataset_reference(path, task, item["expected_identity"], verification)
    return {
        "schema_version": 1,
        "status": "REFERENCE_LIBRARY_READY_EXECUTION_BLOCKED",
        "motion_authorized": False,
        "tasks": references,
    }
