from __future__ import annotations

import pytest

from openclaw_aruco.reference.follower_range_diagnostic import diagnose


def test_detects_body_target_exceeding_follower_calibration() -> None:
    plan = {
        "motion_authorized": False,
        "execution": {"authorized": False},
        "joints": ["wrist_flex.pos", "gripper.pos"],
        "targets": [{"target": [101.0, 50.0]}, {"target": [90.0, 20.0]}],
    }
    ranges = {
        "motion_authorized": False,
        "physical_limits_validated": False,
        "resolution_ticks": 4095,
        "body_range_ticks": {"wrist_flex.pos": [1000, 3095]},
        "gripper_range_ticks": [1000, 2000],
    }
    report = diagnose(plan, ranges)
    assert report["violations"]["wrist_flex.pos"]["above_count"] == 1
    assert report["findings"]["gripper.pos"]["above_count"] == 0
    assert report["motion_authorized"] is False
    assert report["robot_commands_sent"] == 0


def test_refuses_motion_authorized_plan() -> None:
    with pytest.raises(ValueError, match="motion-disabled"):
        diagnose({"motion_authorized": True, "execution": {"authorized": False}}, {})
