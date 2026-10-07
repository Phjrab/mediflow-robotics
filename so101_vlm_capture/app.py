import argparse
import atexit
import json
import threading
import time
from pathlib import Path

import cv2
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.serving import make_server

from so101_capture.camera import MultiCameraReader
from so101_capture.storage import DatasetStore
from so101_capture.validation import validate_labels
from so101_capture.video import VideoTrialRecorder

BASE_DIR = Path(__file__).resolve().parent


def load_camera_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["cameras"]


def create_app(camera=None, store=None, data_dir=None, cameras=None, recorder=None):
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.camera = camera or MultiCameraReader(cameras or load_camera_config(BASE_DIR / "config" / "cameras.json"))
    app.store = store or DatasetStore(data_dir or BASE_DIR / "data" / "pilot")
    app.recorder = recorder or VideoTrialRecorder(app.camera, app.store)
    mapping_path = BASE_DIR / "config" / "medicine_baskets.json"

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        cameras_status = app.camera.status()
        return jsonify({
            "cameras": cameras_status,
            "connected": any(c["connected"] for c in cameras_status),
            "latest": app.store.latest(),
            "latest_trial": app.store.latest_trial(),
            "video": app.recorder.status(),
        })

    @app.get("/api/mapping")
    def mapping():
        return jsonify(json.loads(mapping_path.read_text(encoding="utf-8")))

    @app.get("/video_feed")
    @app.get("/video_feed/<camera_id>")
    def video_feed(camera_id=None):
        def frames():
            while True:
                frame = app.camera.get_frame(camera_id)
                if frame is None:
                    time.sleep(0.15)
                    continue
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n"
                time.sleep(1 / 30)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.post("/api/capture")
    def capture():
        payload = request.get_json(silent=True) or {}
        payload["needs_approval"] = payload.get("needs_approval") is True
        errors = validate_labels(payload)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        camera_id = payload.get("camera_id", "wrist")
        frame = app.camera.get_frame(camera_id)
        camera_status = app.camera.camera_status(camera_id)
        if frame is None:
            return jsonify({"ok": False, "errors": [f"{camera_id}에서 저장 가능한 프레임이 없습니다."]}), 503
        try:
            row = app.store.save(frame, payload, camera=(camera_status or {}).get("device", camera_id))
        except Exception as exc:
            return jsonify({"ok": False, "errors": [f"저장 실패: {exc}"]}), 500
        return jsonify({"ok": True, "annotation": row})

    @app.get("/api/stats")
    def stats():
        return jsonify(app.store.stats())

    @app.get("/api/video/status")
    def video_status():
        return jsonify(app.recorder.status())

    def trial_for_client(trial):
        output = dict(trial)
        output["video_url"] = f"/data/pilot/videos/{Path(str(trial['video'])).name}"
        output["keyframes"] = [
            {
                **frame,
                "image_url": f"/data/pilot/images/{Path(str(frame['image'])).name}",
            }
            for frame in trial.get("keyframes", [])
        ]
        return output

    @app.get("/api/video/trials")
    def video_trials():
        return jsonify({
            "trials": [trial_for_client(row) for row in app.store.list_trials(limit=50)]
        })

    @app.delete("/api/video/trials/<trial_id>")
    def delete_video_trial(trial_id):
        if app.recorder.status().get("active"):
            return jsonify({"ok": False, "error": "녹화 중에는 기존 영상을 삭제할 수 없습니다."}), 409
        expected_video = (request.get_json(silent=True) or {}).get("video")
        if not expected_video:
            return jsonify({"ok": False, "error": "삭제 대상을 먼저 확인해야 합니다."}), 400
        try:
            result = app.store.archive_trial(trial_id, expected_video)
        except KeyError:
            return jsonify({"ok": False, "error": "해당 영상을 찾을 수 없습니다."}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({"ok": True, "deleted": result})

    @app.post("/api/video/start")
    def video_start():
        payload = request.get_json(silent=True) or {}
        payload["needs_approval"] = payload.get("needs_approval") is True
        payload["capture_phase"] = "before_grasp"
        payload["action_result"] = "running"
        errors = validate_labels(payload)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        camera_id = payload.get("camera_id", "wrist")
        camera_status = app.camera.camera_status(camera_id)
        if not camera_status or not camera_status.get("connected"):
            return jsonify({"ok": False, "errors": [f"{camera_id} 카메라가 연결되지 않았습니다."]}), 503
        try:
            state = app.recorder.start(
                payload,
                camera_id,
                camera_status.get("device", camera_id),
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "errors": [str(exc)]}), 409
        return jsonify({"ok": True, "video": state})

    @app.post("/api/video/mark")
    def video_mark():
        phase = (request.get_json(silent=True) or {}).get("capture_phase")
        try:
            state = app.recorder.mark(phase)
        except ValueError as exc:
            return jsonify({"ok": False, "errors": [str(exc)]}), 409
        return jsonify({"ok": True, "video": state})

    @app.post("/api/video/stop")
    def video_stop():
        action_result = (request.get_json(silent=True) or {}).get("action_result")
        if action_result not in {"success", "failure", "human_intervention", "not_recorded"}:
            return jsonify({"ok": False, "errors": ["올바른 최종 동작 결과를 선택하세요."]}), 400
        try:
            saved = app.recorder.stop(action_result)
        except (ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "errors": [str(exc)]}), 409
        return jsonify({"ok": True, **saved})

    @app.get("/api/latest")
    def latest():
        return jsonify({"latest": app.store.latest()})

    @app.delete("/api/latest")
    def delete_latest():
        expected = (request.get_json(silent=True) or {}).get("image")
        if not expected:
            return jsonify({"ok": False, "error": "삭제 대상을 먼저 확인해야 합니다."}), 400
        try:
            deleted = app.store.delete_latest(expected)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({"ok": True, "deleted": deleted})

    @app.get("/data/pilot/images/<path:filename>")
    def image(filename):
        return send_from_directory(app.store.images, filename)

    @app.get("/data/pilot/videos/<path:filename>")
    def video(filename):
        return send_from_directory(app.store.videos, filename)

    @app.post("/api/shutdown")
    def shutdown():
        if app.recorder.status().get("active"):
            return jsonify({"ok": False, "error": "영상 촬영을 먼저 종료한 뒤 서버를 종료하세요."}), 409
        app.recorder.close()
        app.camera.release()
        app.store.close()
        callback = app.config.get("SHUTDOWN_CALLBACK")
        if callback:
            threading.Thread(target=callback, daemon=True).start()
        return jsonify({"ok": True, "message": "카메라를 해제하고 촬영 서버를 종료합니다."})

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--camera-config", default=str(BASE_DIR / "config" / "cameras.json"))
    parser.add_argument("--data-dir", default=str(BASE_DIR / "data" / "pilot"))
    parser.add_argument("--video-fps", type=float, default=10.0)
    parser.add_argument("--video-max-seconds", type=float, default=30.0)
    args = parser.parse_args()
    cameras = load_camera_config(args.camera_config)
    camera = MultiCameraReader(cameras)
    store = DatasetStore(args.data_dir)
    recorder = VideoTrialRecorder(
        camera,
        store,
        fps=args.video_fps,
        max_seconds=args.video_max_seconds,
    )
    app = create_app(camera=camera, store=store, recorder=recorder)
    server = make_server(args.host, args.port, app, threaded=True)
    app.config["SHUTDOWN_CALLBACK"] = server.shutdown
    camera.start()

    def cleanup():
        app.recorder.close()
        camera.release()
        store.close()

    atexit.register(cleanup)
    print(f"SO-101 capture UI: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()
