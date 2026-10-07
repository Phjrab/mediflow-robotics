from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_aruco.scene import inspect_scene, load_config


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "openclaw_aruco/config/dry_run_workspace.json"
LIVE_FRAME = ROOT / "outputs/openclaw_aruco/openclaw_raw_vertical.jpg"


def test_config_is_fail_closed_and_uses_correct_abc_mapping() -> None:
    config = load_config(CONFIG)
    assert config["mode"] == {
        "dry_run": True,
        "robot_enabled": False,
        "motion_authorized": False,
    }
    assert {name: config["tasks"][name]["basket_marker_id"] for name in ("A", "B", "C")} == {
        "A": 6,
        "B": 5,
        "C": 4,
    }
    assert config["transforms"]["T_B_W"] is None
    assert config["robot_limits_mm"] is None
    assert config["bottle_detector"]["fixed_left_to_right_identity"] == ["C", "B", "A"]
    assert config["bottle_detector"]["hough"]["fallback_param2"] == 18.0
    assert config["bottle_detector"]["fixed_identity_x_ranges_px"] == {
        "C": [285, 345],
        "B": [345, 415],
        "A": [415, 480],
    }
    assert all("pickup_pixel" not in config["tasks"][name] for name in ("A", "B", "C"))


def test_live_raw_frame_has_all_markers_and_table_targets() -> None:
    report, _ = inspect_scene(LIVE_FRAME, CONFIG)
    assert report["status"] == "READY_FOR_DRY_RUN"
    assert report["detected_marker_ids"] == list(range(7))
    assert report["reference"]["missing_ids"] == []
    assert report["reference"]["homography_ready"] is True
    assert report["reference"]["rms_mm"] < 0.01
    assert report["bottle_detection"]["candidate_count"] == 3
    assert report["bottle_detection"]["depth_validated"] is False
    for name in ("A", "B", "C"):
        assert len(report["tasks"][name]["pickup_table_xy_mm"]) == 2
        assert len(report["tasks"][name]["basket_table_xy_mm"]) == 2
        assert report["tasks"][name]["robot_base_pickup_xyz_mm"] is None
    assert report["robot_enabled"] is False
    assert report["motion_authorized"] is False


def test_current_user_placement_uses_live_bottle_centers_not_x_guides() -> None:
    frame = ROOT / "outputs/openclaw_aruco/current_raw_after_user_placement_20261005.jpg"
    report, _ = inspect_scene(frame, CONFIG)
    assert report["status"] == "READY_FOR_DRY_RUN"
    assert report["bottle_detection"]["candidate_count"] == 3
    centers = {name: report["tasks"][name]["pickup_pixel"] for name in ("A", "B", "C")}
    assert centers["C"][0] < centers["B"][0] < centers["A"][0]
    assert centers == {"A": [442.5, 210.5], "B": [380.5, 227.5], "C": [312.5, 225.5]}
    assert all(report["tasks"][name]["pickup_coordinate_status"] == "UNVALIDATED_CAP_PROJECTION" for name in ("A", "B", "C"))


def test_robot_enabled_config_is_rejected(tmp_path: Path) -> None:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    document["mode"]["robot_enabled"] = True
    candidate = tmp_path / "unsafe.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="refuses robot-enabled"):
        load_config(candidate)
