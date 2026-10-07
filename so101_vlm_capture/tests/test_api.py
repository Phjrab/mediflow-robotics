import numpy as np

from app import create_app
from so101_capture.storage import DatasetStore


class FakeCamera:
    def __init__(self, frame=True): self.frame = np.zeros((480, 640, 3), dtype=np.uint8) if frame else None
    def get_frame(self, camera_id=None): return None if self.frame is None else self.frame.copy()
    def status(self): return [{"id":"wrist", "connected": self.frame is not None, "error": None, "width":640, "height":480, "fps":30, "device":"fake"}]
    def camera_status(self, camera_id): return self.status()[0]
    def release(self): pass


class FakeRecorder:
    def __init__(self): self.active = False
    def status(self): return {"active": self.active, "trial_id": "trial_000001"} if self.active else {"active": False}
    def start(self, labels, camera_id, camera_device): self.active = True; return self.status()
    def mark(self, phase):
        if not self.active: raise ValueError("영상 촬영 중이 아닙니다.")
        return {**self.status(), "marked_phases": [phase]}
    def stop(self, action_result):
        if not self.active: raise ValueError("영상 촬영 중이 아닙니다.")
        self.active = False
        return {
            "trial": {"trial_id":"trial_000001", "video":"videos/trial_000001.mp4"},
            "annotations": [{"image":"images/scene_000001.jpg"}],
        }
    def close(self): pass


def payload():
    return {
        "camera_id":"wrist", "target_class":"medicine_a", "object_state":"upright",
        "visibility":"visible", "graspability":"graspable", "destination":"basket_1",
        "destination_visibility":"visible", "grasp_strategy":"lid_grasp",
        "required_skill":"pick_upright", "capture_phase":"before_grasp",
        "action_result":"running", "instruction":"medicine_a를 알맞은 바구니에 넣어",
        "needs_approval":True,
    }


def add_trial(store):
    trial_id, temp_video = store.next_trial()
    temp_video.write_bytes(b"video")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    markers = [
        {
            "frame": frame,
            "capture_phase": phase,
            "offset_seconds": index,
            "selection": "manual_marker",
        }
        for index, phase in enumerate(("before_grasp", "grasping", "after_grasp"))
    ]
    trial, _ = store.finalize_trial(
        trial_id, temp_video, markers, {**payload(), "action_result":"success"},
        "fake", "start", "end", 10, 30, 640, 480,
    )
    return trial


def test_one_request_saves_exactly_one_image_and_annotation(tmp_path):
    app = create_app(camera=FakeCamera(), store=DatasetStore(tmp_path)); app.testing = True
    response = app.test_client().post('/api/capture', json=payload())
    assert response.status_code == 200
    assert len(list((tmp_path / 'images').glob('*.jpg'))) == 1
    assert len((tmp_path / 'annotations.jsonl').read_text(encoding='utf-8').splitlines()) == 1


def test_invalid_label_and_missing_camera_are_safe(tmp_path):
    app = create_app(camera=FakeCamera(), store=DatasetStore(tmp_path)); app.testing = True
    bad = payload(); bad.update({"object_state":"fallen", "grasp_strategy":"lid_grasp"})
    assert app.test_client().post('/api/capture', json=bad).status_code == 400
    app.camera = FakeCamera(frame=False)
    assert app.test_client().post('/api/capture', json=payload()).status_code == 503


def test_video_start_mark_stop_flow(tmp_path):
    recorder = FakeRecorder()
    app = create_app(
        camera=FakeCamera(), store=DatasetStore(tmp_path), recorder=recorder
    )
    app.testing = True
    client = app.test_client()
    started = client.post('/api/video/start', json=payload())
    assert started.status_code == 200
    assert started.get_json()["video"]["active"] is True
    marked = client.post('/api/video/mark', json={"capture_phase":"grasping"})
    assert marked.status_code == 200
    assert marked.get_json()["video"]["marked_phases"] == ["grasping"]
    stopped = client.post('/api/video/stop', json={"action_result":"success"})
    assert stopped.status_code == 200
    assert stopped.get_json()["trial"]["trial_id"] == "trial_000001"


def test_shutdown_refuses_while_video_is_recording(tmp_path):
    recorder = FakeRecorder(); recorder.active = True
    app = create_app(
        camera=FakeCamera(), store=DatasetStore(tmp_path), recorder=recorder
    )
    app.testing = True
    response = app.test_client().post('/api/shutdown')
    assert response.status_code == 409


def test_list_and_recoverably_delete_video_trial(tmp_path):
    store = DatasetStore(tmp_path)
    trial = add_trial(store)
    app = create_app(
        camera=FakeCamera(), store=store, recorder=FakeRecorder()
    )
    app.testing = True
    client = app.test_client()
    listed = client.get('/api/video/trials')
    assert listed.status_code == 200
    item = listed.get_json()["trials"][0]
    assert item["trial_id"] == trial["trial_id"]
    assert item["video_url"].endswith("trial_000001.mp4")
    assert len(item["keyframes"]) == 3
    wrong = client.delete(
        f'/api/video/trials/{trial["trial_id"]}', json={"video":"videos/wrong.mp4"}
    )
    assert wrong.status_code == 409
    deleted = client.delete(
        f'/api/video/trials/{trial["trial_id"]}', json={"video":trial["video"]}
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted"]["recoverable"] is True
    assert client.get('/api/video/trials').get_json()["trials"] == []
