from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_aruco.planner import plan_transfer
from openclaw_aruco.scene import load_config


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "openclaw_aruco/config/dry_run_workspace.json"
SCENE = ROOT / "outputs/openclaw_aruco/scene_report_20261005.json"


def _documents() -> tuple[dict, dict]:
    return json.loads(SCENE.read_text(encoding="utf-8")), load_config(CONFIG)


def test_current_configuration_is_blocked_without_waypoints() -> None:
    scene, config = _documents()
    result = plan_transfer(scene, config, "A")
    assert result["status"] == "BLOCKED_SAFETY_CONFIG"
    assert result["waypoints"] == []
    assert result["ik_solutions"] == []
    assert result["execution"] == {"authorized": False, "token": None, "endpoint": None}
    assert any("T_B_W" in reason for reason in result["blocked_reasons"])
    assert any("IK solver" in reason for reason in result["blocked_reasons"])


@pytest.mark.parametrize(
    ("task", "identity", "marker_id"),
    [("A", "large", 6), ("B", "medium", 5), ("C", "small", 4)],
)
def test_abc_identity_and_basket_mapping_are_preserved(task: str, identity: str, marker_id: int) -> None:
    scene, config = _documents()
    result = plan_transfer(scene, config, task)
    assert result["identity"] == identity
    assert result["basket_marker_id"] == marker_id


@pytest.mark.parametrize("task", ["", "D", "A; reboot", "all", "1"])
def test_arbitrary_task_names_are_rejected(task: str) -> None:
    scene, config = _documents()
    with pytest.raises(ValueError, match="exactly A, B, or C"):
        plan_transfer(scene, config, task)


def test_robot_enabled_scene_is_rejected() -> None:
    scene, config = _documents()
    scene["robot_enabled"] = True
    with pytest.raises(ValueError, match="robot-enabled scene"):
        plan_transfer(scene, config, "C")


def test_complete_cartesian_fixture_still_cannot_execute_without_ik() -> None:
    scene, config = _documents()
    config["transforms"]["T_B_W"] = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    config["robot_limits_mm"] = {"x": [0.0, 500.0], "y": [0.0, 500.0], "z": [0.0, 500.0]}
    config["heights_mm"] = {"pickup_z": 20.0, "approach_z": 100.0, "basket_drop_z": 50.0}
    config["ik"] = {"solver": "fixture-only", "joint_limits_deg": {"joint": [-180.0, 180.0]}}
    config["motion_blocked_reasons"] = []
    result = plan_transfer(scene, config, "B")
    assert result["status"] == "BLOCKED_IK_IMPLEMENTATION"
    assert len(result["waypoints"]) == 8
    assert result["ik_solutions"] == []
    assert result["execution"]["authorized"] is False
