from __future__ import annotations

import time
from pathlib import Path

import pytest

from openclaw_aruco.safe_replay_runtime import run_with_safety_runtime, validate_authorization


def authorization() -> dict:
    return {
        "status": "AUTHORIZED_SINGLE_SUPERVISED_RUN",
        "motion_authorized": True,
        "task": "A",
        "plan_sha256": "abc",
        "power_cutoff_ready": True,
        "user_confirmation": "배치 유지, 전원차단 준비",
        "nonce": "0123456789abcdef",
        "expires_at_unix": time.time() + 60,
        "max_run_seconds": 10,
    }


def plan() -> dict:
    return {
        "task": "A",
        "status": "PHYSICALLY_VALIDATED_SUPERVISED_PATH",
        "motion_authorized": False,
        "blocking_reasons": [],
        "physical_safety_validation": {
            "tcp": True,
            "robot_world_transform": True,
            "workspace": True,
            "joint_limits": True,
            "calibrated_action_range": True,
            "stop_path": True,
        },
        "joints": ["one.pos", "two.pos"],
        "control": {"control_hz": 20, "estimated_duration_seconds": 1,
                    "source_action_integrity": {"source_actions_modified": False}},
        "targets": [{"target": [0, 0]}, {"target": [0.1, 0.2]}],
    }


def test_authorization_rejects_wrong_plan_hash() -> None:
    with pytest.raises(ValueError, match="exact plan"):
        validate_authorization(authorization(), plan(), "different", time.time())


def test_current_dry_run_status_cannot_be_authorized() -> None:
    current = plan()
    current["status"] = "DRY_RUN_RECORDED_PATH_READY_EXECUTION_BLOCKED"
    with pytest.raises(ValueError, match="physically validated"):
        validate_authorization(authorization(), current, "abc", time.time())


def test_remaining_physical_blocker_cannot_be_authorized() -> None:
    current = plan()
    current["blocking_reasons"] = ["TCP is only nominal"]
    with pytest.raises(ValueError, match="blocking reasons"):
        validate_authorization(authorization(), current, "abc", time.time())


def test_unchecked_calibrated_action_range_cannot_be_authorized() -> None:
    current = plan()
    current["physical_safety_validation"]["calibrated_action_range"] = False
    with pytest.raises(ValueError, match="physical safety validation"):
        validate_authorization(authorization(), current, "abc", time.time())


def test_stop_file_prevents_first_target(tmp_path: Path) -> None:
    stop = tmp_path / "stop"
    stop.touch()
    sent = []
    result = run_with_safety_runtime(
        plan(), authorization(), "abc", sent.append, stop, tmp_path / "lock",
        sleeper=lambda _: None,
    )
    assert result["reason"] == "stop_requested"
    assert result["targets_sent"] == 0
    assert sent == []


def test_mock_run_sends_all_targets(tmp_path: Path) -> None:
    sent = []
    result = run_with_safety_runtime(
        plan(), authorization(), "abc", sent.append, tmp_path / "stop", tmp_path / "lock",
        sleeper=lambda _: None,
    )
    assert result["status"] == "RUN_FINISHED"
    assert len(sent) == 2
