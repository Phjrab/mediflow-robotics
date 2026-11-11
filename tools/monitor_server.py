"""Tailnet dashboard and operator feedback. Does not open robot hardware."""

import argparse
import json
import math
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from corrections import CAMERAS, CLIP_ID
from vla_monitor import COMMAND_SOCKET, STATE_DIR


ASSETS = Path(__file__).with_name("monitor_web")
ACTIVE = {"observing", "predicting", "moving", "correcting", "saving_correction", "operator_ready"}
CORRECTION_TEXT = {"조금 더 앞으로": "forward", "그리퍼 열어": "gripper_open"}
CLIP_FILES = {"manifest.json", "frames.jsonl", *(f"{role}.mp4" for role in CAMERAS)}
MAX_SNAPSHOT = 4 * 1024 * 1024
NOTE_ID = re.compile(r"[a-f0-9]{32}")
LELAB_JOINTS = {"Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw"}


def read_object(path):
    try:
        with path.open("rb") as source:
            data = json.loads(source.read(MAX_SNAPSHOT))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_summary(root):
    for path in sorted(root.rglob("summary.json"), reverse=True):
        record = read_object(path)
        if record.get("status") in ("HOLD", "RUN_COMPLETE"):
            return {"phase": record["status"].lower(), "run": path.parent.name,
                    "reason": record.get("reason"), "step": record.get("steps"),
                    "hold": record.get("hold"), "historical": True, "updated": path.stat().st_mtime}
    return {"phase": "idle"}


def explain(reason):
    if not reason:
        return None
    for terms, title, followup in (
        (("통신 코드=-7", "통신 코드 -7"), "모터 상태 패킷 손상",
         "모터 응답 검증에서 중단했습니다. 아래 오류 원문과 위치 유지 결과를 확인하세요."),
        (("통신 코드=-6", "통신 코드 -6"), "모터 응답 시간 초과",
         "Jetson에서 모터의 응답을 받지 못했습니다. 팔로워 DC 전원과 첫 모터까지의 케이블을 확인한 뒤 모터 응답을 다시 읽어 보세요."),
        (("정지 요청", "Ctrl+C"), "운영자가 정지 요청",
         "새 정책 목표 전송을 중단했습니다. 아래 위치 유지 결과를 확인하세요."),
        (("temperature_c", "온도"), "모터 온도 또는 상태 검사에서 중단",
         "표시된 관절의 온도와 상태를 확인하세요. 단일 값만으로 센서 오독 여부를 판단할 수 없습니다."),
        (("카메라", "프레임", "camera"), "새 카메라 입력을 받지 못함",
         "카메라 연결, 영상 밝기, 장치 점유 상태를 확인하세요."),
        (("토크", "위치 모드", "보정"), "시작 조건 불충족",
         "팔로워 토크, 위치 모드 또는 보정 상태를 확인하세요."),
        (("추론", "policy"), "정책 추론 중단",
         "최근 추론 시간과 아래 오류 원문을 확인하세요."),
    ):
        if any(term in reason for term in terms):
            return {"title": title, "followup": followup}
    return {"title": "실행기 중단", "followup": "아래 오류 원문에서 중단된 검사나 장치를 확인하세요."}


class MonitorServer(ThreadingHTTPServer):
    def __init__(self, address, directory, control_client, history_root):
        super().__init__(address, Viewer)
        self.directory = directory
        self.control_client = control_client
        self.host = f"{address[0]}:{self.server_port}"
        self.origin = f"http://{self.host}"
        self.token = secrets.token_urlsafe(24)
        self.notes = directory / "feedback"
        self.note_lock = threading.Lock()
        self.correction_lock = threading.Lock()
        self.correction_pending = None
        self.last_run = last_summary(history_root)
        self.motor_poll_after = 0
        self.motor_check_lock = threading.Lock()

    def status(self):
        return read_object(self.directory / "status.json") or self.last_run.copy()

    def present_status(self, state):
        reason = state.get("reason") or ""
        if state.get("phase") != "hold" or not any(code in reason for code in ("통신 코드=-6", "통신 코드 -6")):
            return state
        path = self.directory / "bus-health.json"
        with self.motor_check_lock:
            health = read_object(path)
            if health.get("checked_at", 0) <= state.get("updated", 0) and time.monotonic() >= self.motor_poll_after:
                self.motor_poll_after = time.monotonic() + 2
                try:
                    with urlopen("http://127.0.0.1:8022/joint-positions", timeout=0.35) as response:
                        sample = json.loads(response.read(8192))
                    joints = sample.get("joint_positions", {})
                    stamp = sample.get("timestamp", 0)
                    if (sample.get("success") is True and set(joints) == LELAB_JOINTS
                            and all(type(value) in (int, float) and math.isfinite(value) for value in joints.values())
                            and type(stamp) in (int, float) and 0 <= time.time() - stamp < 2):
                        health = {"checked_at": stamp, "source": "lelab", "joints": 6}
                        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                        pending = path.with_suffix(".pending")
                        pending.write_text(json.dumps(health), encoding="utf-8")
                        pending.replace(path)
                except (OSError, ValueError, TypeError):
                    pass
        checked_at = health.get("checked_at", 0)
        if (health.get("source") == "lelab" and health.get("joints") == 6
                and state.get("updated", 0) < checked_at <= time.time()):
            return {**state, "phase": "idle", "historical": False, "reason": None,
                    "previous_reason": reason, "motor_recovered_at": checked_at}
        return state


class Viewer(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, media_type, body, *, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data, *, filename=None):
        self.respond(status, "application/json; charset=utf-8",
                     json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                     filename=filename)

    def is_operator(self):
        return self.client_address[0] == self.server.control_client

    def hold_available(self, state):
        return (state.get("phase") in ACTIVE and time.time() - state.get("updated", 0) < 2
                and (self.server.directory / COMMAND_SOCKET).is_socket())

    def correction_available(self, state):
        return (self.hold_available(state) and state.get("phase") in ("moving", "operator_ready")
                and bool(state.get("correction_kinds")))

    def do_GET(self):
        if self.headers.get("Host") != self.server.host:
            self.send_json(400, {"error": "잘못된 관제 화면 주소"})
            return
        if self.path == "/api/status":
            data = self.server.present_status(self.server.status())
            operator = self.is_operator()
            data.update(server_time=time.time(), diagnosis=explain(data.get("reason")),
                        operator=operator, can_hold=operator and self.hold_available(data),
                        can_correct=operator and self.correction_available(data))
            if operator:
                data["control_token"] = self.server.token
            self.send_json(200, data)
            return
        if self.path == "/api/observation":
            self.send_json(200, read_object(self.server.directory / "observation.json"))
            return
        if self.path.startswith("/api/corrections/"):
            parts = self.path.split("/")
            if (self.is_operator() and len(parts) == 5 and CLIP_ID.fullmatch(parts[3])
                    and parts[4] in CLIP_FILES):
                clip = self.server.directory / "corrections" / parts[3]
                if (clip / "manifest.json").is_file():
                    filename = parts[4]
                    try:
                        with (clip / filename).open("rb") as source:
                            body = source.read(16 * 1024 * 1024 + 1)
                        if len(body) <= 16 * 1024 * 1024:
                            media = "video/mp4" if filename.endswith(".mp4") else "application/octet-stream"
                            self.respond(200, media, body, filename=filename)
                            return
                    except OSError:
                        pass
            self.send_json(404, {"error": "교정 영상을 찾을 수 없습니다"})
            return
        if self.path.startswith("/api/feedback/"):
            note_id = self.path.rsplit("/", 1)[-1]
            note = (read_object(self.server.notes / f"{note_id}.json")
                    if self.is_operator() and NOTE_ID.fullmatch(note_id) else {})
            if note.get("review_state") != "operator_note":
                self.send_json(404, {"error": "교정 기록을 찾을 수 없습니다"})
                return
            self.send_json(200, note, filename=f"so101-correction-{note_id}.json")
            return
        assets = {"/": ("index.html", "text/html"), "/screen.css": ("screen.css", "text/css"),
                  "/screen.js": ("screen.js", "text/javascript")}
        if self.path not in assets:
            self.respond(404, "text/plain", b"Not found")
            return
        name, media_type = assets[self.path]
        self.respond(200, media_type + "; charset=utf-8", (ASSETS / name).read_bytes())

    def do_POST(self):
        if (self.headers.get("Host") != self.server.host
                or self.headers.get("Origin") != self.server.origin
                or not self.is_operator()
                or not secrets.compare_digest(self.headers.get("X-VLA-Token", ""), self.server.token)):
            self.send_json(403, {"error": "이 노트북에서 관제 화면을 다시 열어 주세요"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 4096:
                raise ValueError
            self.connection.settimeout(3)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, OSError):
            self.send_json(400, {"error": "요청 형식 오류"})
            return
        handlers = {"/api/hold": self.request_hold, "/api/mark": self.mark_scene,
                    "/api/feedback": self.save_note, "/api/correct": self.request_correction}
        handler = handlers.get(self.path)
        if handler is None:
            self.send_json(404, {"error": "지원하지 않는 명령"})
            return
        try:
            handler(data)
        except OSError:
            self.send_json(503, {"error": "Jetson에서 요청을 처리하지 못했습니다"})

    def request_hold(self, data):
        state = self.server.status()
        if not self.hold_available(state) or data.get("run") != state.get("run"):
            self.send_json(409, {"error": "정지할 실행이 없거나 상태가 갱신되지 않습니다"})
            return
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.settimeout(0.2)
            sender.sendto(f"hold:{state['run']}".encode("ascii"),
                          str(self.server.directory / COMMAND_SOCKET))
        self.send_json(202, {"status": "requested"})

    def request_correction(self, data):
        text = data.get("text")
        normalized = " ".join(text.strip().rstrip(".!").split()) if isinstance(text, str) else ""
        kind = CORRECTION_TEXT.get(normalized)
        if not kind or data.get("supervised") is not True:
            self.send_json(400, {"error": "지원 명령을 입력하고 현장 감독을 확인하세요"})
            return
        with self.server.correction_lock:
            state = self.server.status()
            if (not self.correction_available(state) or data.get("run") != state.get("run")
                    or kind not in state["correction_kinds"]):
                self.send_json(409, {"error": "교정이 허용된 실행 또는 등록한 앞으로 경로가 필요합니다"})
                return
            previous = self.server.correction_pending
            now = time.time()
            if (previous and previous["run"] == state["run"] and now - previous["requested"] < 2
                    and (state.get("correction_result") or {}).get("id") != previous["id"]):
                self.send_json(409, {"error": "앞선 교정 요청의 처리 결과를 기다려 주세요"})
                return
            request = {"id": secrets.token_hex(16), "run": state["run"], "requested": now,
                       "kind": kind, "text": normalized}
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                sender.settimeout(0.2)
                sender.sendto(json.dumps(request).encode("ascii"),
                              str(self.server.directory / COMMAND_SOCKET))
            self.server.correction_pending = request
        self.send_json(202, {"id": request["id"], "status": "requested"})

    def mark_scene(self, data):
        state = self.server.status()
        observation = read_object(self.server.directory / "observation.json")
        if (observation.get("preview") or not observation.get("images") or data.get("run") != observation.get("run")
                or state.get("run") != observation.get("run")
                or data.get("capture") != observation.get("capture")):
            self.send_json(409, {"error": "영상이 갱신되었습니다. 현재 장면을 다시 고정하세요"})
            return
        note_id = secrets.token_hex(16)
        record = {
            "schema": "so101.vla.operator_feedback.v1", "id": note_id,
            "review_state": "draft", "marked_at_utc": datetime.now(timezone.utc).isoformat(),
            "observation": observation, "state_at_mark": state,
        }
        self.server.notes.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.server.notes / f"{note_id}.json").open("x", encoding="utf-8") as target:
            json.dump(record, target, ensure_ascii=False, allow_nan=False)
        self.send_json(201, {"id": note_id, "capture": observation["capture"], "step": state.get("step")})

    def save_note(self, data):
        note_id, text = data.get("id"), data.get("text")
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id) or not isinstance(text, str) or not 1 <= len(text.strip()) <= 600:
            self.send_json(400, {"error": "장면을 고정하고 600자 이하로 교정 내용을 입력하세요"})
            return
        path = self.server.notes / f"{note_id}.json"
        with self.server.note_lock:
            note = read_object(path)
            if note.get("review_state") == "operator_note" and note.get("instruction") == text.strip():
                self.send_json(200, {"id": note_id, "status": "saved"})
                return
            if note.get("review_state") != "draft":
                self.send_json(409, {"error": "이미 저장했거나 고정된 장면을 찾을 수 없습니다"})
                return
            note.update(instruction=text.strip(), review_state="operator_note",
                        saved_at_utc=datetime.now(timezone.utc).isoformat())
            pending = path.with_suffix(".pending")
            pending.write_text(json.dumps(note, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            pending.replace(path)
        self.send_json(200, {"id": note_id, "status": "saved"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--state-dir", type=Path, default=STATE_DIR)
    parser.add_argument("--control-client", required=True)
    args = parser.parse_args()
    with MonitorServer((args.bind, args.port), args.state_dir, args.control_client, Path(__file__).resolve().parents[1]/'runs') as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
