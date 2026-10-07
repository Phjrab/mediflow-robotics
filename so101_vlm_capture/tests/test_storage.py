import json

import numpy as np
import pytest

from so101_capture.storage import DatasetStore


def labels():
    return {
        "target_class": "medicine_b", "object_state": "fallen", "visibility": "visible",
        "graspability": "graspable", "destination": "basket_2", "destination_visibility": "visible",
        "grasp_strategy": "body_grasp", "capture_phase": "before_grasp", "action_result": "running",
        "required_skill": "pick_fallen", "instruction": "medicine_b를 알맞은 바구니에 넣어",
        "needs_approval": True,
    }


def test_save_restart_numbering_and_delete_one(tmp_path):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    first_store = DatasetStore(tmp_path)
    first = first_store.save(frame, labels())
    assert first["image"] == "images/scene_000001.jpg"
    assert (tmp_path / first["image"]).exists()

    restarted = DatasetStore(tmp_path)
    second = restarted.save(frame, labels())
    assert second["image"] == "images/scene_000002.jpg"
    assert (tmp_path / first["image"]).exists()
    assert len((tmp_path / "annotations.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    with pytest.raises(ValueError):
        restarted.delete_latest(first["image"])
    restarted.delete_latest(second["image"])
    assert (tmp_path / first["image"]).exists()
    assert not (tmp_path / second["image"]).exists()
    rows = [json.loads(line) for line in (tmp_path / "annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["image"] for row in rows] == [first["image"]]


def test_stats_reports_missing_and_orphaned(tmp_path):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    store = DatasetStore(tmp_path)
    row = store.save(frame, labels())
    (tmp_path / row["image"]).unlink()
    (tmp_path / "images" / "scene_999999.jpg").write_bytes(b"orphan")
    stats = store.stats()
    assert row["image"] in stats["missing_images"]
    assert "images/scene_999999.jpg" in stats["orphaned_images"]


def test_finalize_video_trial_saves_video_metadata_and_three_keyframes(tmp_path):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    store = DatasetStore(tmp_path)
    trial_id, temp_video = store.next_trial()
    temp_video.write_bytes(b"test-video")
    markers = [
        {
            "frame": frame,
            "capture_phase": phase,
            "offset_seconds": index * 1.5,
            "selection": "manual_marker",
        }
        for index, phase in enumerate(("before_grasp", "grasping", "after_grasp"))
    ]
    trial, annotations = store.finalize_trial(
        trial_id,
        temp_video,
        markers,
        {**labels(), "action_result": "success"},
        camera="fake",
        started_at="2026-09-23T00:00:00+00:00",
        ended_at="2026-09-23T00:00:06+00:00",
        fps=10,
        frame_count=60,
        width=640,
        height=480,
    )
    assert trial["video"] == "videos/trial_000001.mp4"
    assert (tmp_path / trial["video"]).read_bytes() == b"test-video"
    assert len(annotations) == 3
    assert [row["answer"]["capture_phase"] for row in annotations] == [
        "before_grasp", "grasping", "after_grasp"
    ]
    assert annotations[-1]["answer"]["action_result"] == "success"
    assert store.stats()["video_trials"] == 1
    deleted = store.archive_trial(trial_id, trial["video"])
    assert deleted["recoverable"] is True
    archive = tmp_path / deleted["archived_to"]
    assert (archive / "trial_000001.mp4").read_bytes() == b"test-video"
    assert len(list((archive / "images").glob("scene_*.jpg"))) == 3
    assert not (tmp_path / trial["video"]).exists()
    assert store.stats()["video_trials"] == 0
    assert store.stats()["total"] == 0
    next_trial_id, _ = store.next_trial()
    assert next_trial_id == "trial_000002"
    next_image = store.save(frame, labels())
    assert next_image["image"] == "images/scene_000004.jpg"
