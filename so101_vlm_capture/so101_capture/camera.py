import threading
import time

import cv2


class CameraReader:
    def __init__(self, device, width=640, height=480, fps=30, retry_seconds=2.0):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.retry_seconds = retry_seconds
        self._capture = None
        self._frame = None
        self._error = "카메라 연결 대기 중"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"camera-{self.device}")
        self._thread.start()

    def _open(self):
        if not self.device:
            return None
        if str(self.device).startswith("http://") or str(self.device).startswith("https://"):
            cap = cv2.VideoCapture(self.device)
        else:
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _run(self):
        while not self._stop.is_set():
            if self._capture is None:
                self._capture = self._open()
                if self._capture is None:
                    self._set_error(f"{self.device or '장치 미지정'}를 열 수 없습니다. 장치/권한/점유 상태를 확인하세요.")
                    self._stop.wait(self.retry_seconds)
                    continue
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self._set_error("프레임 읽기에 실패했습니다. 카메라 재연결을 시도합니다.")
                self._capture.release()
                self._capture = None
                self._stop.wait(self.retry_seconds)
                continue
            actual_height, actual_width = frame.shape[:2]
            if (actual_width, actual_height) != (self.width, self.height):
                self._set_error(f"해상도 불일치: {actual_width}×{actual_height} (요청 {self.width}×{self.height})")
            else:
                with self._lock:
                    self._frame = frame.copy()
                    self._error = None
            time.sleep(0.001)

    def _set_error(self, message):
        with self._lock:
            self._error = message

    def get_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def status(self):
        with self._lock:
            return {
                "connected": self._frame is not None and self._error is None,
                "error": self._error,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "device": self.device,
            }

    def release(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class MultiCameraReader:
    def __init__(self, cameras):
        self.cameras = cameras
        self.readers = {item['id']: CameraReader(item['device']) for item in cameras}

    def start(self):
        for reader in self.readers.values():
            reader.start()

    def get_frame(self, camera_id=None):
        if camera_id in self.readers:
            return self.readers[camera_id].get_frame()
        for camera in self.cameras:
            frame = self.readers[camera['id']].get_frame()
            if frame is not None:
                return frame
        return None

    def status(self):
        result = []
        for camera in self.cameras:
            entry = dict(camera)
            entry.update(self.readers[camera['id']].status())
            result.append(entry)
        return result

    def camera_status(self, camera_id):
        for item in self.status():
            if item['id'] == camera_id:
                return item
        return None

    def release(self):
        for reader in self.readers.values():
            reader.release()
