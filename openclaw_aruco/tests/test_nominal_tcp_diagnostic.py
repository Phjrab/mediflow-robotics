from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openclaw_aruco.reference.nominal_tcp_diagnostic import nominal_frame, diagnose


ROOT = Path(__file__).resolve().parents[2]


def test_nominal_frame_uses_official_candidate_offset() -> None:
    config = json.loads((ROOT / "openclaw_aruco/reference/nominal_gripper_frame.json").read_text())
    transform = nominal_frame(config)
    assert np.allclose(transform[:3, 3], [-0.0079, -0.000218121, -0.0981274])
    assert np.isclose(np.linalg.det(transform[:3, :3]), 1.0)


def test_nominal_diagnostic_never_authorizes_motion() -> None:
    plan = {
        "motion_authorized": False,
        "execution": {"authorized": False},
        "joints": [*list({
            "shoulder_pan.pos": 0,
            "shoulder_lift.pos": 0,
            "elbow_flex.pos": 0,
            "wrist_flex.pos": 0,
            "wrist_roll.pos": 0,
        }), "gripper.pos"],
        "targets": [{"target": [0, 0, 0, 0, 0, 0]}],
        "control": {"normalized_command_limits": {"clipped_source_values": 0}},
    }
    config = json.loads((ROOT / "openclaw_aruco/reference/nominal_gripper_frame.json").read_text())
    report = diagnose(plan, ROOT / "openclaw_aruco/reference/so101_new_calib.urdf", config)
    assert report["status"] == "NOMINAL_TCP_DIAGNOSTIC_ONLY"
    assert report["motion_authorized"] is False
    assert report["robot_commands_sent"] == 0
    assert report["target_count"] == 1
    plan["motion_authorized"] = True
    with pytest.raises(ValueError, match="non-executable"):
        diagnose(plan, ROOT / "openclaw_aruco/reference/so101_new_calib.urdf", config)
