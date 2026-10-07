"""Fail-closed transfer planner for saved-scene reports.

The module performs math only.  It has no camera, robot, serial, torque, or
network imports and never sends a command to hardware.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np

from openclaw_aruco.scene import TASK_NAMES


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and bool(np.isfinite(value))


def _valid_transform(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    matrix = np.asarray(value, dtype=np.float64)
    return matrix.shape == (4, 4) and bool(np.isfinite(matrix).all()) and bool(
        np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-6)
    )


def _valid_limits(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for axis in ("x", "y", "z"):
        bounds = value.get(axis)
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or not all(_finite_number(item) for item in bounds)
            or bounds[0] >= bounds[1]
        ):
            return False
    return True


def _transform_xyz(transform: list[list[float]], xyz: list[float]) -> list[float]:
    point = np.asarray([*xyz, 1.0], dtype=np.float64)
    transformed = np.asarray(transform, dtype=np.float64) @ point
    if abs(float(transformed[3])) < 1e-9:
        raise ValueError("invalid homogeneous transform result")
    return [round(float(value / transformed[3]), 3) for value in transformed[:3]]


def _inside_limits(xyz: list[float], limits: dict[str, list[float]]) -> bool:
    return all(limits[axis][0] <= value <= limits[axis][1] for axis, value in zip(("x", "y", "z"), xyz))


def plan_transfer(scene: dict[str, Any], config: dict[str, Any], task_name: str) -> dict[str, Any]:
    """Return a non-executable plan or explicit blocking reasons.

    Even a complete result remains ``DRY_RUN_ONLY`` and carries no execution
    token.  The caller must not interpret this document as motion authority.
    """

    task = task_name.strip().upper()
    if task not in TASK_NAMES:
        raise ValueError("task must be exactly A, B, or C")
    mode = config.get("mode", {})
    if mode.get("dry_run") is not True or mode.get("robot_enabled") is not False:
        raise ValueError("planner accepts dry-run, robot-disabled configs only")
    if mode.get("motion_authorized") is not False:
        raise ValueError("planner refuses a motion-authorized config")
    if scene.get("robot_enabled") is not False or scene.get("motion_authorized") is not False:
        raise ValueError("planner refuses a robot-enabled scene report")

    blocked: list[str] = []
    if scene.get("status") != "READY_FOR_DRY_RUN":
        blocked.append(f"scene is not ready: {scene.get('status', 'UNKNOWN')}")
    scene_task = scene.get("tasks", {}).get(task)
    if not isinstance(scene_task, dict):
        blocked.append(f"scene report has no task {task}")
        scene_task = {}

    pickup_xy = scene_task.get("pickup_table_xy_mm")
    basket_xy = scene_task.get("basket_table_xy_mm")
    if not (isinstance(pickup_xy, list) and len(pickup_xy) == 2 and all(_finite_number(v) for v in pickup_xy)):
        blocked.append("pickup table coordinate is unavailable")
    if not (isinstance(basket_xy, list) and len(basket_xy) == 2 and all(_finite_number(v) for v in basket_xy)):
        blocked.append("basket table coordinate is unavailable")

    transform = config.get("transforms", {}).get("T_B_W")
    if not _valid_transform(transform):
        blocked.append("T_B_W is missing or invalid")
    limits = config.get("robot_limits_mm")
    if not _valid_limits(limits):
        blocked.append("robot workspace XYZ limits are missing or invalid")
    heights = config.get("heights_mm", {})
    for key in ("pickup_z", "approach_z", "basket_drop_z"):
        if not _finite_number(heights.get(key)):
            blocked.append(f"{key} is not measured")
    if all(_finite_number(heights.get(key)) for key in ("pickup_z", "approach_z", "basket_drop_z")):
        if heights["approach_z"] <= max(heights["pickup_z"], heights["basket_drop_z"]):
            blocked.append("approach_z must be above pickup_z and basket_drop_z")

    ik = config.get("ik", {})
    if not ik.get("solver"):
        blocked.append("validated IK solver is not configured")
    if not isinstance(ik.get("joint_limits_deg"), dict) or not ik.get("joint_limits_deg"):
        blocked.append("joint limits are not configured")

    # Preserve operator/environment assumptions as blockers until they are
    # explicitly resolved in a reviewed config, rather than guessing here.
    for reason in config.get("motion_blocked_reasons", []):
        if reason not in blocked:
            blocked.append(reason)

    result: dict[str, Any] = {
        "schema_version": 1,
        "task": task,
        "identity": scene_task.get("identity"),
        "basket_marker_id": scene_task.get("basket_marker_id"),
        "status": "BLOCKED_SAFETY_CONFIG" if blocked else "DRY_RUN_ONLY",
        "blocked_reasons": blocked,
        "waypoints": [],
        "ik_solutions": [],
        "execution": {
            "authorized": False,
            "token": None,
            "endpoint": None,
        },
        "source_scene": {
            "image": scene.get("image"),
            "image_sha256": scene.get("image_sha256"),
        },
    }
    if blocked:
        return result

    assert isinstance(pickup_xy, list) and isinstance(basket_xy, list)
    assert isinstance(transform, list) and isinstance(limits, dict)
    table_waypoints = [
        ("approach_pick", [pickup_xy[0], pickup_xy[1], heights["approach_z"]], "open"),
        ("descend_pick", [pickup_xy[0], pickup_xy[1], heights["pickup_z"]], "open"),
        ("grasp", [pickup_xy[0], pickup_xy[1], heights["pickup_z"]], "close"),
        ("lift", [pickup_xy[0], pickup_xy[1], heights["approach_z"]], "close"),
        ("approach_basket", [basket_xy[0], basket_xy[1], heights["approach_z"]], "close"),
        ("descend_drop", [basket_xy[0], basket_xy[1], heights["basket_drop_z"]], "close"),
        ("release", [basket_xy[0], basket_xy[1], heights["basket_drop_z"]], "open"),
        ("retreat", [basket_xy[0], basket_xy[1], heights["approach_z"]], "open"),
    ]
    waypoints = []
    for index, (name, table_xyz, gripper) in enumerate(table_waypoints):
        robot_xyz = _transform_xyz(transform, table_xyz)
        if not _inside_limits(robot_xyz, limits):
            result["status"] = "BLOCKED_WORKSPACE_LIMIT"
            result["blocked_reasons"].append(f"{name} is outside robot workspace limits")
            result["waypoints"] = []
            return result
        waypoints.append(
            {
                "index": index,
                "name": name,
                "table_xyz_mm": [round(float(v), 3) for v in table_xyz],
                "robot_base_xyz_mm": robot_xyz,
                "gripper": gripper,
            }
        )
    result["waypoints"] = waypoints
    result["status"] = "BLOCKED_IK_IMPLEMENTATION"
    result["blocked_reasons"] = [
        "Cartesian dry-run waypoints are valid, but IK calculation is not implemented in this bridge"
    ]
    return result


def detached_copy(document: dict[str, Any]) -> dict[str, Any]:
    """Return a copy useful to callers/tests without shared mutable state."""

    return deepcopy(document)
