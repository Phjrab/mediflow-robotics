#!/usr/bin/env python3
"""Evaluate a recorded dry-run path in a nominal CAD gripper frame only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from openclaw_aruco.reference.urdf_forward_kinematics import (
    UrdfKinematics,
    homogeneous,
    rpy_rotation,
)


PLAN_TO_URDF = {
    "shoulder_pan.pos": "Rotation",
    "shoulder_lift.pos": "Pitch",
    "elbow_flex.pos": "Elbow",
    "wrist_flex.pos": "Wrist_Pitch",
    "wrist_roll.pos": "Wrist_Roll",
}


def nominal_frame(config: dict[str, Any]) -> np.ndarray:
    if config.get("physical_tcp_validated") is not False or config.get("motion_authorized") is not False:
        raise ValueError("nominal frame must remain physically unvalidated and motion-disabled")
    xyz = np.asarray(config.get("translation_m"), dtype=np.float64)
    rpy = np.asarray(config.get("rpy_rad"), dtype=np.float64)
    if xyz.shape != (3,) or rpy.shape != (3,) or not np.isfinite(xyz).all() or not np.isfinite(rpy).all():
        raise ValueError("nominal frame must have finite translation and RPY triples")
    return homogeneous(rpy_rotation(rpy), xyz)


def urdf_limits(path: Path) -> dict[str, tuple[float, float]]:
    result = {}
    for joint in ET.parse(path).getroot().findall("joint"):
        limit = joint.find("limit")
        if limit is not None and joint.attrib.get("type") == "revolute":
            result[joint.attrib["name"]] = (float(limit.attrib["lower"]), float(limit.attrib["upper"]))
    return result


def diagnose(plan: dict[str, Any], urdf_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    if plan.get("motion_authorized") is not False or plan.get("execution", {}).get("authorized") is not False:
        raise ValueError("source plan must be a non-executable dry-run artifact")
    names = plan.get("joints")
    if not isinstance(names, list) or set(names) != {*PLAN_TO_URDF, "gripper.pos"}:
        raise ValueError("unexpected plan joint names")
    targets = plan.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("plan has no targets")
    offset = nominal_frame(config)
    kinematics = UrdfKinematics.from_file(urdf_path)
    limits = urdf_limits(urdf_path)
    cartesian = []
    violations: dict[str, dict[str, float]] = {}
    for item in targets:
        values = np.asarray(item.get("target"), dtype=np.float64)
        if values.shape != (len(names),) or not np.isfinite(values).all():
            raise ValueError("invalid target vector")
        joint_rad = {PLAN_TO_URDF[name]: math.radians(float(values[names.index(name)])) for name in PLAN_TO_URDF}
        for name, angle in joint_rad.items():
            lower, upper = limits[name]
            if angle < lower - 1e-6 or angle > upper + 1e-6:
                entry = violations.setdefault(name, {"observed_min_deg": math.inf, "observed_max_deg": -math.inf,
                                                     "urdf_min_deg": math.degrees(lower), "urdf_max_deg": math.degrees(upper)})
                entry["observed_min_deg"] = min(entry["observed_min_deg"], math.degrees(angle))
                entry["observed_max_deg"] = max(entry["observed_max_deg"], math.degrees(angle))
        tool_pose = kinematics.forward(joint_rad, "base", "gripper") @ offset
        cartesian.append(tool_pose[:3, 3] * 1000.0)

    positions = np.asarray(cartesian, dtype=np.float64)
    max_step_mm = float(np.linalg.norm(np.diff(positions, axis=0), axis=1).max()) if len(positions) > 1 else 0.0
    control = plan.get("control", {})
    clipped = control.get("normalized_command_limits", {}).get("clipped_source_values", 0)
    outside_old_bounds = control.get("source_action_integrity", {}).get("outside_old_unvalidated_bounds", 0)
    blocked = [
        "official-model gripper frame is only a nominal candidate, not this robot's validated grasp TCP",
        "robot-to-table transform, approach/pickup/drop heights, and workspace limits are not validated",
        "recorded-path start pose and live scene must be rechecked immediately before any motion",
    ]
    if violations:
        blocked.append("recorded trajectory exceeds the local URDF nominal joint limits")
    if clipped:
        blocked.append("source action values were clipped when the dry-run plan was constructed")
    if outside_old_bounds:
        blocked.append("source values exceed old unvalidated bounds; real motor and collision limits remain unverified")
    return {
        "schema_version": 1,
        "status": "NOMINAL_TCP_DIAGNOSTIC_ONLY",
        "motion_authorized": False,
        "robot_commands_sent": 0,
        "source_plan_status": plan.get("status"),
        "source_urdf_sha256": hashlib.sha256(urdf_path.read_bytes()).hexdigest(),
        "nominal_frame": config,
        "target_count": len(positions),
        "cartesian_envelope_mm": {
            "min": np.round(positions.min(axis=0), 3).tolist(),
            "max": np.round(positions.max(axis=0), 3).tolist(),
        },
        "maximum_cartesian_step_mm": round(max_step_mm, 3),
        "urdf_joint_limit_violations": violations,
        "source_values_clipped": clipped,
        "source_values_outside_old_unvalidated_bounds": outside_old_bounds,
        "blocking_reasons": blocked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--urdf", required=True, type=Path)
    parser.add_argument("--frame-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if not args.output.parent.is_dir():
        parser.error(f"output parent does not exist: {args.output.parent}")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    config = json.loads(args.frame_config.read_text(encoding="utf-8"))
    report = diagnose(plan, args.urdf, config)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "target_count", "maximum_cartesian_step_mm", "blocking_reasons")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
