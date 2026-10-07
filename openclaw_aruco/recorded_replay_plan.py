"""Create a non-executable, step-limited plan from one recorded episode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from openclaw_aruco.reference_gate import POSE_NAME_MAP


def interpolate_limited(
    start: np.ndarray,
    target: np.ndarray,
    body_step: float,
    gripper_step: float,
) -> list[np.ndarray]:
    """Return interpolated targets after ``start`` with per-joint step limits."""

    delta = np.asarray(target, dtype=np.float64) - np.asarray(start, dtype=np.float64)
    limits = np.full(delta.shape, body_step, dtype=np.float64)
    limits[-1] = gripper_step
    segments = max(1, int(np.ceil(np.max(np.abs(delta) / limits))))
    return [start + delta * (index / segments) for index in range(1, segments + 1)]


def preserve_source_actions(raw_actions: np.ndarray) -> tuple[np.ndarray, int]:
    """Keep calibrated degree actions intact while flagging old assumed bounds."""
    actions = np.asarray(raw_actions, dtype=np.float64)
    if actions.ndim != 2 or actions.shape[1] < 2 or not np.isfinite(actions).all():
        raise ValueError("source actions must be a finite 2D joint array")
    legacy_lower = np.asarray([-100.0] * (actions.shape[1] - 1) + [0.0])
    legacy_upper = np.asarray([100.0] * actions.shape[1])
    exceedances = int(np.count_nonzero((actions < legacy_lower) | (actions > legacy_upper)))
    return actions.copy(), exceedances


def build_recorded_replay_plan(
    dataset_dir: Path,
    episode_index: int,
    current_pose: dict[str, Any],
    task: str,
    source_scene: dict[str, Any],
    body_step: float = 0.5,
    gripper_step: float = 1.0,
    control_hz: float = 20.0,
) -> dict[str, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required to read the LeRobot dataset") from exc

    if task not in {"A", "B", "C"}:
        raise ValueError("task must be exactly A, B, or C")
    if current_pose.get("motion_authorized") is not False:
        raise ValueError("current pose input must keep motion_authorized=false")
    if source_scene.get("status") != "READY_FOR_DRY_RUN" or source_scene.get("motion_authorized") is not False:
        raise ValueError("source scene is not a motion-disabled ready dry-run scene")
    if body_step <= 0 or gripper_step <= 0 or control_hz <= 0:
        raise ValueError("step limits and control rate must be positive")

    info = json.loads((dataset_dir / "meta" / "info.json").read_text(encoding="utf-8"))
    joint_names = info["features"]["action"]["names"]
    data_files = sorted((dataset_dir / "data").glob("chunk-*/*.parquet"))
    data = pa.concat_tables([
        pq.read_table(path, columns=["action", "episode_index"]) for path in data_files
    ])
    episode_ids = np.asarray(data.column("episode_index").to_numpy(), dtype=np.int64)
    raw_actions = np.asarray(data.column("action").to_pylist(), dtype=np.float64)[episode_ids == episode_index]
    # SO-101 body positions use MotorNormMode.DEGREES by default. The old
    # [-100, 100] assumption was not a validated joint limit and silently
    # changed recorded actions. Keep the source exact; never treat this dry-run
    # path as an executable command envelope.
    actions, legacy_bound_exceedances = preserve_source_actions(raw_actions)
    if len(actions) < 2:
        raise ValueError(f"episode {episode_index} has fewer than two action frames")
    live_joints = current_pose.get("joints", {})
    current = np.asarray([live_joints[POSE_NAME_MAP[name]] for name in joint_names], dtype=np.float64)

    targets: list[dict[str, Any]] = []
    previous = current
    for phase, phase_targets in (
        ("slow_align_to_recorded_start", [actions[0]]),
        ("recorded_episode", list(actions[1:])),
    ):
        for original_index, target in enumerate(phase_targets):
            interpolated = interpolate_limited(previous, np.asarray(target), body_step, gripper_step)
            for value in interpolated:
                targets.append({
                    "index": len(targets),
                    "phase": phase,
                    "source_action_index": 0 if phase.startswith("slow_align") else original_index + 1,
                    "target": np.round(value, 5).tolist(),
                })
            previous = np.asarray(target, dtype=np.float64)

    target_array = np.asarray([item["target"] for item in targets], dtype=np.float64)
    steps = np.vstack([current, target_array])
    max_step = np.max(np.abs(np.diff(steps, axis=0)), axis=0)
    limits = np.asarray([body_step] * (len(joint_names) - 1) + [gripper_step])
    if np.any(max_step > limits + 1e-6):
        raise AssertionError("generated plan exceeds configured step limits")

    align_steps = sum(item["phase"] == "slow_align_to_recorded_start" for item in targets)
    return {
        "schema_version": 1,
        "task": task,
        "status": "DRY_RUN_RECORDED_PATH_READY_EXECUTION_BLOCKED",
        "motion_authorized": False,
        "source": {
            "dataset": str(dataset_dir),
            "episode": episode_index,
            "scene_image": source_scene.get("image"),
            "scene_image_sha256": source_scene.get("image_sha256"),
        },
        "joints": joint_names,
        "control": {
            "control_hz": control_hz,
            "body_max_step": body_step,
            "gripper_max_step": gripper_step,
            "original_frames": len(actions),
            "generated_targets": len(targets),
            "slow_alignment_targets": align_steps,
            "estimated_duration_seconds": round(len(targets) / control_hz, 3),
            "maximum_generated_step": np.round(max_step, 5).tolist(),
            "source_action_integrity": {
                "body_units": "degrees_from_calibrated_motor_bus",
                "gripper_units": "calibrated_0_to_100",
                "source_actions_modified": False,
                "outside_old_unvalidated_bounds": legacy_bound_exceedances,
                "physical_joint_limits_validated": False,
            },
        },
        "start": {
            "live_pose": np.round(current, 5).tolist(),
            "recorded_first_action": np.round(actions[0], 5).tolist(),
            "slow_alignment_delta": np.round(actions[0] - current, 5).tolist(),
        },
        "trajectory_envelope": {
            "min": np.round(target_array.min(axis=0), 5).tolist(),
            "max": np.round(target_array.max(axis=0), 5).tolist(),
        },
        "targets": targets,
        "execution": {"authorized": False, "endpoint": None, "token": None},
        "blocking_reasons": [
            "generated targets are an offline dry-run artifact and are not connected to a robot endpoint",
            "the source scene hash and live joint pose must be re-read immediately before any supervised test",
            "a reviewed stop path, single-run lock, timeout, and explicit user approval are still required",
            "physical joint limits are not established; recorded degree values are preserved without clipping",
        ],
    }
