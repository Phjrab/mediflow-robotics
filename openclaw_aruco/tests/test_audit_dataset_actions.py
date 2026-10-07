from __future__ import annotations

import numpy as np
import pytest

from openclaw_aruco.reference.audit_dataset_actions import summarize_actions


def test_counts_frames_and_distinct_episodes_outside_range() -> None:
    result = summarize_actions(
        np.array([[91.0, 10.0], [92.0, 20.0], [0.0, 30.0]]),
        np.array([0, 0, 1]),
        ["wrist_flex.pos", "gripper.pos"],
        {"wrist_flex.pos": [1000, 3047]},
        4095,
    )
    assert result["frames"] == 3
    assert result["episodes"] == 2
    assert result["joint_findings"]["wrist_flex.pos"]["exceeding_frames"] == 2
    assert result["joint_findings"]["wrist_flex.pos"]["affected_episodes"] == 1


def test_rejects_unmatched_joints() -> None:
    with pytest.raises(ValueError, match="joint names"):
        summarize_actions(np.array([[0.0]]), np.array([0]), ["unknown"], {}, 4095)
