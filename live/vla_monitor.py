"""Publish snapshots for the local SO-101 viewer without opening any device."""

import base64
import io
import json
import logging
import socket
import threading
import time
from pathlib import Path


STATE_DIR = Path.home() / ".cache" / "so101-vla-monitor"
INTERVAL = 0.2
COMMAND_SOCKET = "control.sock"


class Monitor:
    def __init__(self, run, task, joints, directory=STATE_DIR, correction_kinds=(), *, controls=True):
        self.run, self.task, self.joints = run, task, list(joints)
        self.directory = directory
        self.snapshot = self.observation = None
        self.capture_id = 0
        self.next_update = 0.0
        self.commands = None
        self.controls = controls
        self.hold_received = False
        self.correction_kinds = tuple(correction_kinds)
        self.pending_correction = self.correction_result = self.latest_clip = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._write, daemon=True, name="vla-viewer")
        self.thread.start()

    def status(self, phase, *, step=0, sample=None, model=None, sent=None,
               inference_seconds=None, reason=None, force=False):
        now = time.monotonic()
        if not force and now < self.next_update:
            return
        self.next_update = now + INTERVAL
        sample = sample or {}
        self.snapshot = {
            "run": self.run, "task": self.task, "joints": self.joints, "phase": phase,
            "updated": time.time(), "capture": self.capture_id, "step": step,
            "state": sample.get("state"), "ticks": sample.get("present_raw"),
            "temperature": sample.get("temperatures"), "model": model, "sent": sent,
            "voltage_v": sample.get("voltage_v"),
            "thermal_diagnostics": sample.get("thermal_diagnostics"),
            "temperature_protection": sample.get("temperature_protection"),
            "inference_seconds": inference_seconds, "reason": reason,
            "correction_kinds": self.correction_kinds, "correction_result": self.correction_result,
            "latest_clip": self.latest_clip,
        }

    def capture(self, frames, state, *, preview=False):
        self.capture_id += 1
        self.observation = (self.capture_id, time.time(), state, frames, preview or not self.controls)

    def hold_requested(self):
        if self.hold_received or self.commands is None:
            return self.hold_received
        for _ in range(16):
            try:
                message = self.commands.recv(1024)
            except OSError:
                break
            if message == f"hold:{self.run}".encode("ascii"):
                self.hold_received = True
                self.pending_correction = None
                break
            try:
                data = json.loads(message)
                from corrections import CLIP_ID

                if (self.pending_correction is None and data["run"] == self.run
                        and data["kind"] in self.correction_kinds and CLIP_ID.fullmatch(data["id"])
                        and 0 <= time.time() - data["requested"] < 2):
                    self.pending_correction = data
            except (ValueError, TypeError, KeyError):
                continue
        return self.hold_received

    def take_correction(self):
        if self.hold_requested():
            return None
        command, self.pending_correction = self.pending_correction, None
        if command is not None and time.time() - command["requested"] < 2:
            return command
        return None

    def _save(self, name, data):
        destination = self.directory / name
        pending = destination.with_suffix(".pending")
        pending.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        pending.replace(destination)

    def _write(self):
        last_state = last_observation = None
        command_path = self.directory / COMMAND_SOCKET
        receiver = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            if self.controls:
                try:
                    if command_path.is_socket():
                        command_path.unlink()
                    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                    receiver.bind(str(command_path))
                    command_path.chmod(0o600)
                    receiver.setblocking(False)
                    self.commands = receiver
                except OSError:
                    logging.getLogger(__name__).exception("VLA 화면의 정지 채널을 열지 못했습니다")
            while True:
                snapshot, observation = self.snapshot, self.observation
                if snapshot is not None and snapshot is not last_state:
                    self._save("status.json", snapshot)
                    last_state = snapshot
                if observation is not None and observation is not last_observation:
                    from PIL import Image

                    number, captured, state, frames, preview = observation
                    images = {}
                    for role, frame in frames.items():
                        output = io.BytesIO()
                        Image.fromarray(frame["pixels"], mode="RGB").save(output, "JPEG", quality=78)
                        images[role] = base64.b64encode(output.getvalue()).decode("ascii")
                    self._save("observation.json", {
                        "run": self.run, "capture": number, "captured": captured,
                        "state": state, "images": images, "preview": preview,
                    })
                    last_observation = observation
                if self.stop.is_set() and self.snapshot is last_state and self.observation is last_observation:
                    return
                self.stop.wait(INTERVAL)
        except Exception:
            logging.getLogger(__name__).exception("VLA 화면 상태 기록 중단")
        finally:
            if receiver is not None:
                receiver.close()
            if self.commands is not None:
                command_path.unlink(missing_ok=True)
            self.commands = None

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1.0)

    def finish(self, status, reason, step, hold=None):
        self.snapshot = {**(self.snapshot or {}), "phase": status.lower(),
                         "reason": reason, "step": step, "updated": time.time(),
                         "capture": self.capture_id, "hold": hold,
                         "correction_result": self.correction_result, "latest_clip": self.latest_clip}
        self.close()
