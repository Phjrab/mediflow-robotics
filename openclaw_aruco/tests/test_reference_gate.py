from __future__ import annotations

from pathlib import Path

from openclaw_aruco.reference_gate import compare_reference_gate


def test_reference_gate_blocks_scene_and_pose_mismatch(tmp_path: Path, monkeypatch) -> None:
    reference = {
        "motion_authorized": False,
        "tasks": {
            task: {
                "joints": [
                    "shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
                    "wrist_flex.pos", "wrist_roll.pos", "gripper.pos",
                ],
                "reference": {
                    "representative_episode": 1,
                    "representative_start_observation_pose": [0, 0, 0, 0, 0, 0],
                },
            }
            for task in ("A", "B", "C")
        },
    }
    current_scene = {"status": "READY_FOR_DRY_RUN", "tasks": {task: {"pickup_pixel": [20, 20]} for task in "ABC"}}
    pose = {"unit": "degrees_or_gripper_units", "joints": {
        "shoulder_pan": 10, "shoulder_lift": 0, "elbow_flex": 0,
        "wrist_flex": 0, "wrist_roll": 0, "gripper": 0,
    }}
    config = {
        "motion_authorized": False,
        "maximum_bottle_pixel_error": 2,
        "maximum_start_pose_error": {name: 3 for name in reference["tasks"]["A"]["joints"]},
        "reference_start_images": {task: f"{task}.jpg" for task in "ABC"},
    }
    monkeypatch.setattr(
        "openclaw_aruco.reference_gate.inspect_scene",
        lambda *_: ({"tasks": {task: {"pickup_pixel": [0, 0]} for task in "ABC"}}, None),
    )
    result = compare_reference_gate(current_scene, pose, reference, config, tmp_path / "scene.json", tmp_path)
    assert result["motion_authorized"] is False
    assert all(item["status"] == "REFERENCE_GATE_BLOCKED" for item in result["tasks"].values())
