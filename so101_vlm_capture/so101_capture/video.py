import threading
import time
from datetime import datetime, timezone

import cv2


PHASES = ("before_grasp", "grasping", "after_grasp")
AUTO_FRACTIONS = {
    "before_grasp": 0.15,
    "grasping": 0.50,
    "after_grasp": 0.80,
}


class VideoTrialRecorder:
    """Record one bounded camera trial without controlling the robot."""

    def __init__(self, camera, store, fps=10.0, max_seconds=30.0):
        self.camera = camera
        self.store = store
        self.fps = float(fps)
        self.max_seconds = float(max_seconds)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._state = None

    def status(self):
        with self._lock:
            if self._state is None:
                return {"active": False}
            state = self._state
            return {
                "active": True,
                "trial_id": state["trial_id"],
                "camera_id": state["camera_id"],
                "started_at": state["started_at"],
                "elapsed_seconds": round(time.monotonic() - state["started_monotonic"], 1),
                "frame_count": state["frame_count"],
                "marked_phases": list(state["markers"]),
                "error": state.get("error"),
                "max_seconds": self.max_seconds,
            }

    def start(self, labels, camera_id, camera_device):
        with self._lock:
            if self._state is not None:
                raise ValueError("이미 영상 촬영 중입니다.")
            frame = self.camera.get_frame(camera_id)
            if frame is None:
                raise ValueError(f"{camera_id}에서 저장 가능한 프레임이 없습니다.")
            height, width = frame.shape[:2]
            trial_id, temp_path = self.store.next_trial()
            writer = cv2.VideoWriter(
                str(temp_path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                self.fps,
                (width, height),
            )
            if not writer.isOpened():
                writer.release()
                raise RuntimeError("MP4 영상 파일을 열 수 없습니다. OpenCV 코덱을 확인하세요.")
            now = datetime.now(timezone.utc).isoformat()
            self._stop.clear()
            self._state = {
                "trial_id": trial_id,
                "temp_path": temp_path,
                "camera_id": camera_id,
                "camera_device": camera_device,
                "labels": dict(labels),
                "started_at": now,
                "started_monotonic": time.monotonic(),
                "writer": writer,
                "frame_count": 0,
                "width": width,
                "height": height,
                "markers": {},
                "error": None,
            }
            # The initial still is a reliable pre-grasp fallback.
            self._state["markers"]["before_grasp"] = {
                "frame": frame.copy(),
                "offset_seconds": 0.0,
                "capture_phase": "before_grasp",
                "selection": "automatic_start",
            }
            self._thread = threading.Thread(
                target=self._record_loop,
                daemon=True,
                name=f"video-{trial_id}",
            )
            self._thread.start()
            return self.status()

    def _record_loop(self):
        interval = 1.0 / self.fps
        next_frame_at = time.monotonic()
        while not self._stop.is_set():
            with self._lock:
                state = self._state
                if state is None:
                    return
                elapsed = time.monotonic() - state["started_monotonic"]
                if elapsed >= self.max_seconds:
                    state["error"] = "최대 녹화 시간에 도달했습니다. 녹화 종료를 눌러 저장하세요."
                    self._stop.set()
                    break
                camera_id = state["camera_id"]
            frame = self.camera.get_frame(camera_id)
            if frame is not None:
                with self._lock:
                    if self._state is not None:
                        self._state["writer"].write(frame)
                        self._state["frame_count"] += 1
            next_frame_at += interval
            self._stop.wait(max(0.0, next_frame_at - time.monotonic()))

    def mark(self, phase):
        if phase not in PHASES:
            raise ValueError("허용되지 않은 영상 단계입니다.")
        with self._lock:
            if self._state is None:
                raise ValueError("영상 촬영 중이 아닙니다.")
            frame = self.camera.get_frame(self._state["camera_id"])
            if frame is None:
                raise ValueError("현재 카메라 프레임을 가져올 수 없습니다.")
            offset = time.monotonic() - self._state["started_monotonic"]
            self._state["markers"][phase] = {
                "frame": frame.copy(),
                "offset_seconds": offset,
                "capture_phase": phase,
                "selection": "manual_marker",
            }
            return self.status()

    @staticmethod
    def _read_frame(path, fraction, fallback_frame):
        capture = cv2.VideoCapture(str(path))
        try:
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if count <= 0:
                return fallback_frame.copy(), 0.0
            index = min(count - 1, max(0, round((count - 1) * fraction)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                return fallback_frame.copy(), index
            return frame, index
        finally:
            capture.release()

    def stop(self, action_result):
        with self._lock:
            if self._state is None:
                raise ValueError("영상 촬영 중이 아닙니다.")
            state = self._state
            state["labels"]["action_result"] = action_result
            self._stop.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            state["writer"].release()
            if state["frame_count"] <= 0:
                self._state = None
                raise RuntimeError("영상 프레임이 저장되지 않았습니다.")
            fallback = self.camera.get_frame(state["camera_id"])
            if fallback is None:
                fallback = state["markers"]["before_grasp"]["frame"]
            for phase in PHASES:
                if phase in state["markers"]:
                    continue
                frame, index = self._read_frame(
                    state["temp_path"], AUTO_FRACTIONS[phase], fallback
                )
                state["markers"][phase] = {
                    "frame": frame,
                    "offset_seconds": index / self.fps,
                    "capture_phase": phase,
                    "selection": "automatic_fraction",
                }
            markers = [state["markers"][phase] for phase in PHASES]
            ended_at = datetime.now(timezone.utc).isoformat()
            try:
                trial, annotations = self.store.finalize_trial(
                    state["trial_id"],
                    state["temp_path"],
                    markers,
                    state["labels"],
                    state["camera_device"],
                    state["started_at"],
                    ended_at,
                    self.fps,
                    state["frame_count"],
                    state["width"],
                    state["height"],
                )
            finally:
                self._state = None
                self._thread = None
            return {"trial": trial, "annotations": annotations}

    def close(self):
        """Stop writing but preserve the incomplete temporary file for recovery."""
        with self._lock:
            if self._state is None:
                return
            self._stop.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            if self._state is not None:
                self._state["writer"].release()
