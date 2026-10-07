from __future__ import annotations

from openclaw_aruco.check_live_readiness import check_live_readiness


def fake_fetch(endpoint: str) -> dict:
    return {
        "health": {"status": "ok"},
        "robots/so-101": {"robot": {"cameras": [
            {"name": "ceiling_vertical", "camera_index": 8},
            {"name": "ceiling_oblique", "camera_index": 4},
            {"name": "end_effector", "camera_index": 6},
        ]}},
        "available-cameras": {"cameras": [
            {"index": 8, "available": True},
            {"index": 2, "available": True},
        ]},
        "teleoperation-status": {"teleoperation_active": False,
                                 "safety": {"body_start_max_delta_deg": 15.0}},
        "inference-status": {"inference_active": False},
        "recording-status": {"recording_active": False},
        "calibration-status": {"calibration_active": False},
    }[endpoint]


def test_not_enumerated_camera_devices_remain_unverified() -> None:
    result = check_live_readiness(
        {"mode": {"robot_enabled": False, "motion_authorized": False},
         "motion_blocked_reasons": ["TCP uncalibrated"]},
        {"blocking_reasons": ["URDF mismatch"]},
        fake_fetch,
    )
    assert result["site_connected"] is True
    assert result["not_enumerated_camera_devices"] == {"ceiling_oblique": 4, "end_effector": 6}
    assert "unverified" in " ".join(result["blocked_reasons"])
    assert "TCP uncalibrated" in result["blocked_reasons"]
    assert "URDF mismatch" in result["blocked_reasons"]
    assert any("3-degree gate" in reason for reason in result["blocked_reasons"])
    assert result["motion_authorized"] is False
    assert result["robot_commands_sent"] == 0


def test_failed_status_probe_stays_blocked() -> None:
    def fail_on_inference(endpoint: str) -> dict:
        if endpoint == "inference-status":
            raise OSError("offline")
        return fake_fetch(endpoint)

    result = check_live_readiness({"mode": {}}, {}, fail_on_inference)
    assert "inference" in " ".join(result["blocked_reasons"])
    assert result["active_flags"]["inference"] is None
    assert result["motion_authorized"] is False
