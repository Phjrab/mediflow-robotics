#!/usr/bin/env python3
"""Compare a saved, motion-disabled plan with the follower's recorded calibration range."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def diagnose(plan: dict[str, Any], ranges: dict[str, Any]) -> dict[str, Any]:
    if plan.get("motion_authorized") is not False or plan.get("execution", {}).get("authorized") is not False:
        raise ValueError("only a motion-disabled dry-run plan may be inspected")
    if ranges.get("motion_authorized") is not False or ranges.get("physical_limits_validated") is not False:
        raise ValueError("calibration range must remain physically unvalidated and motion-disabled")
    names = plan.get("joints")
    body_ranges = ranges.get("body_range_ticks")
    if not isinstance(names, list) or not isinstance(body_ranges, dict):
        raise ValueError("missing plan joints or body ranges")
    if set(names) != {*body_ranges, "gripper.pos"} or len(names) != len(body_ranges) + 1:
        raise ValueError("plan and calibration joint names do not match")
    resolution = ranges.get("resolution_ticks")
    if resolution != 4095:
        raise ValueError("unexpected motor resolution; verify LeRobot motor model")
    targets = plan.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("plan has no targets")

    calibrated_bounds: dict[str, tuple[float, float]] = {}
    for name, raw in body_ranges.items():
        if not isinstance(raw, list) or len(raw) != 2 or not all(type(v) is int for v in raw):
            raise ValueError(f"invalid calibration range for {name}")
        low, high = raw
        if not 0 <= low < high <= resolution:
            raise ValueError(f"calibration range outside encoder for {name}")
        half_width_deg = (high - low) * 180.0 / resolution
        calibrated_bounds[name] = (-half_width_deg, half_width_deg)
    gripper_range = ranges.get("gripper_range_ticks")
    if not isinstance(gripper_range, list) or len(gripper_range) != 2 or not all(type(v) is int for v in gripper_range):
        raise ValueError("invalid gripper calibration range")
    if not 0 <= gripper_range[0] < gripper_range[1] <= resolution:
        raise ValueError("gripper calibration range outside encoder")
    calibrated_bounds["gripper.pos"] = (0.0, 100.0)

    values_by_name = {name: [] for name in names}
    for item in targets:
        values = item.get("target")
        if not isinstance(values, list) or len(values) != len(names):
            raise ValueError("invalid target vector")
        for name, value in zip(names, values):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("non-finite target value")
            values_by_name[name].append(float(value))

    findings = {}
    for name in names:
        low, high = calibrated_bounds[name]
        observed = values_by_name[name]
        below = sum(v < low for v in observed)
        above = sum(v > high for v in observed)
        findings[name] = {
            "recorded_min": round(min(observed), 5),
            "recorded_max": round(max(observed), 5),
            "calibrated_min": round(low, 5),
            "calibrated_max": round(high, 5),
            "below_count": below,
            "above_count": above,
            "maximum_excess": round(max(0.0, low - min(observed), max(observed) - high), 5),
        }
    violations = {name: info for name, info in findings.items() if info["below_count"] or info["above_count"]}
    return {
        "schema_version": 1,
        "status": "RECORDED_ACTION_VS_FOLLOWER_CALIBRATION_ONLY",
        "target_count": len(targets),
        "unit_body": "degrees",
        "unit_gripper": "0_to_100",
        "findings": findings,
        "violations": violations,
        "interpretation": "LeRobot's degree-mode target conversion does not itself clip to calibrated range; this is not a physical-limit or collision-safety certification.",
        "blocking_reasons": [
            "recorded actions exceed current follower calibration range" if violations else "current follower physical limits remain unvalidated",
            "actual TCP, workspace, transform, collision and stop path remain unvalidated",
        ],
        "motion_authorized": False,
        "robot_commands_sent": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--ranges", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if not args.output.parent.is_dir():
        parser.error(f"output parent does not exist: {args.output.parent}")
    report = diagnose(json.loads(args.plan.read_text()), json.loads(args.ranges.read_text()))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "target_count", "violations", "blocking_reasons")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
