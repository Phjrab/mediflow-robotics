"""Camera-only Jetson integration check. Invoke on an idle service with the operator's camera map."""
from contextlib import ExitStack
from itertools import islice
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

from fastapi.testclient import TestClient
from guarded_app import app, record, server
from lerobot.cameras.configs import Cv2Backends
from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig


def check_idle():
    for route, key in (("recording-status","recording_active"),
                       ("teleoperation-status","teleoperation_active"), ("inference-status","inference_active")):
        with urlopen("http://127.0.0.1:8022/" + route, timeout=3) as response:
            if json.load(response)[key]:
                raise RuntimeError("Production service in use")


def main():
    mapping = dict(end_effector=0, ceiling_vertical=8, ceiling_oblique=6)
    config = {k: {"camera_index": v} for k, v in mapping.items()}
    cameras = {}
    check_idle()
    with ExitStack() as stack:
        stack.enter_context(patch("serial.Serial.open", side_effect=AssertionError("No robot access")))
        client = stack.enter_context(TestClient(app))
        for key, index in mapping.items():
            cam = OpenCVCamera(OpenCVCameraConfig(index_or_path=index, fps=30, width=640, height=480,
                                                  backend=Cv2Backends.V4L2))
            cam.connect()
            stack.callback(cam.disconnect)
            cameras[key] = cam
        stack.enter_context(patch.object(record,"recording_config",SimpleNamespace(cameras=config)))
        stack.enter_context(patch.object(record,"current_robot",SimpleNamespace(cameras=cameras)))
        stack.enter_context(patch.object(record,"recording_active",True))
        stack.enter_context(patch.object(server,"_linux_cameras",side_effect=AssertionError("device re-probe")))
        source = record.camera_feed_frames
        stack.enter_context(patch.object(record,"camera_feed_frames",lambda key: islice(source(key), 2)))
        for key, index in mapping.items():
            for path in (f"/camera-preview/{index}", f"/camera-feed/{key}"):
                response = client.get(path)
                assert response.status_code == 200 and response.content.count(b"--frame\r\n") == 2, path
        snapshot = client.get("/camera-snapshot/8")
        assert snapshot.status_code == 200 and snapshot.content.startswith(b"\xff\xd8")
        for page in ("/", "/recording"):
            assert "/camera-snapshot/8?" in client.get(page, headers={"Accept":"text/html"}).text
        start = time.monotonic()
        frames = 0
        scans = 0
        while time.monotonic() - start < 60:
            if frames % 60 == 0:
                check_idle()
                listed = client.get("/available-cameras").json()["cameras"]
                assert {c["name"]:c["index"] for c in listed} == mapping
                scans += 1
            for cam in cameras.values():
                assert cam.async_read().shape == (480, 640, 3)
            frames += 1
        print(f"duration={time.monotonic()-start:.2f}s frames_per_camera={frames} scans={scans} streaming_paths=6")


if __name__ == "__main__":
    main()
