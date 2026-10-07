"""Fail-closed live-scene and start-pose comparison for dataset references."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from openclaw_aruco.scene import inspect_scene


POSE_NAME_MAP = {
    "shoulder_pan.pos": "shoulder_pan",
    "shoulder_lift.pos": "shoulder_lift",
    "elbow_flex.pos": "elbow_flex",
    "wrist_flex.pos": "wrist_flex",
    "wrist_roll.pos": "wrist_roll",
    "gripper.pos": "gripper",
}


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def compare_reference_gate(
    current_scene: dict[str, Any],
    current_pose: dict[str, Any],
    reference_library: dict[str, Any],
    gate_config: dict[str, Any],
    scene_config_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    if gate_config.get("motion_authorized") is not False:
        raise ValueError("reference gate config must keep motion_authorized=false")
    if current_pose.get("unit") != "degrees_or_gripper_units":
        raise ValueError("current pose unit is invalid")
    pose_values = current_pose.get("joints")
    if not isinstance(pose_values, dict):
        raise ValueError("current pose must contain a joints object")
    if current_scene.get("status") != "READY_FOR_DRY_RUN":
        raise ValueError("current scene is not ready for dry-run comparison")
    if reference_library.get("motion_authorized") is not False:
        raise ValueError("reference library must keep motion_authorized=false")

    pixel_limit = gate_config.get("maximum_bottle_pixel_error")
    pose_limits = gate_config.get("maximum_start_pose_error")
    if not _finite(pixel_limit) or pixel_limit <= 0 or not isinstance(pose_limits, dict):
        raise ValueError("invalid reference gate limits")

    task_results = {}
    for task in ("A", "B", "C"):
        reference = reference_library["tasks"][task]
        joint_names = reference["joints"]
        start_pose = reference["reference"]["representative_start_observation_pose"]
        if len(joint_names) != len(start_pose):
            raise ValueError(f"task {task} reference joint shape mismatch")

        pose_deltas = {}
        pose_violations = {}
        for name, expected in zip(joint_names, start_pose):
            live_name = POSE_NAME_MAP.get(name)
            if live_name is None or not _finite(pose_values.get(live_name)) or not _finite(pose_limits.get(name)):
                raise ValueError(f"missing current pose or limit for {name}")
            delta = float(pose_values[live_name]) - float(expected)
            pose_deltas[name] = round(delta, 4)
            if abs(delta) > float(pose_limits[name]):
                pose_violations[name] = {
                    "delta": round(delta, 4),
                    "limit": float(pose_limits[name]),
                }

        image_path = Path(gate_config["reference_start_images"][task])
        if not image_path.is_absolute():
            image_path = repository_root / image_path
        recorded_scene, _ = inspect_scene(image_path, scene_config_path)
        current_pixel = current_scene["tasks"][task]["pickup_pixel"]
        recorded_pixel = recorded_scene["tasks"][task]["pickup_pixel"]
        if not (
            isinstance(current_pixel, list)
            and isinstance(recorded_pixel, list)
            and len(current_pixel) == len(recorded_pixel) == 2
        ):
            raise ValueError(f"task {task} bottle pixel is unavailable")
        pixel_delta = [float(current_pixel[i]) - float(recorded_pixel[i]) for i in range(2)]
        pixel_error = math.hypot(*pixel_delta)
        scene_violation = pixel_error > float(pixel_limit)

        blocked_reasons = []
        if scene_violation:
            blocked_reasons.append(
                f"{task} bottle pixel error {pixel_error:.3f}px exceeds {float(pixel_limit):.3f}px"
            )
        if pose_violations:
            blocked_reasons.append(f"{task} start pose exceeds {len(pose_violations)} joint limits")
        task_results[task] = {
            "status": "REFERENCE_GATE_MATCH" if not blocked_reasons else "REFERENCE_GATE_BLOCKED",
            "representative_episode": reference["reference"]["representative_episode"],
            "bottle_pixel": {
                "current": current_pixel,
                "recorded": recorded_pixel,
                "delta_current_minus_recorded": [round(value, 3) for value in pixel_delta],
                "error_px": round(pixel_error, 3),
                "limit_px": float(pixel_limit),
            },
            "start_pose": {
                "current": {name: pose_values[live] for name, live in POSE_NAME_MAP.items()},
                "recorded": dict(zip(joint_names, start_pose)),
                "delta_current_minus_recorded": pose_deltas,
                "violations": pose_violations,
            },
            "blocked_reasons": blocked_reasons,
        }

    return {
        "schema_version": 1,
        "status": "DRY_RUN_REFERENCE_GATE_COMPLETE",
        "motion_authorized": False,
        "tasks": task_results,
        "execution": {"authorized": False, "endpoint": None, "token": None},
    }
