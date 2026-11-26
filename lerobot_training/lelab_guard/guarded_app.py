"""Separate LeLab entry point. Only the recording module is overlaid."""
import asyncio
from pathlib import Path
import lelab
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

overlay = Path(__file__).resolve().parent/"overlay"
if not (overlay/"record.py").is_file():
    raise RuntimeError("Missing guarded recording module")
lelab.__path__.insert(0,str(overlay))
from lelab import record
from lelab import rollout, server, teleoperate
from lelab.server import app

camera_handoff = asyncio.Lock()


def recording_snapshot(index):
    import cv2

    robot = record.current_robot
    config = record.recording_config
    if robot and config:
        for name, settings in config.cameras.items():
            camera = robot.cameras.get(name)
            if camera is None or str(settings.get("camera_index")) != index:
                continue
            try:
                frame = camera.read_latest()
            except (TimeoutError, RuntimeError):
                break
            ok, image = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            if ok:
                return Response(image.tobytes(), media_type="image/jpeg", headers={"Cache-Control":"no-store"})
    return JSONResponse({"detail": "Waiting for a recording frame"}, status_code=503)

if Path(record.__file__).resolve() != overlay/"record.py":
    raise RuntimeError("Unguarded recording module already imported")


@app.on_event("shutdown")
async def stop_guarded_recording():
    if record.recording_active:
        record.handle_stop_recording()
        if record.recording_thread:
            await asyncio.to_thread(record.recording_thread.join)


@app.middleware("http")
async def guard_version(request, call_next):
    path = request.url.path
    start = request.method == "POST" and path in ("/start-recording", "/guarded-start-recording")
    discovery = request.method == "GET" and path == "/available-cameras"
    preview = request.method == "GET" and path.startswith(("/camera-preview/", "/camera-snapshot/"))
    if start:
        request.scope["path"] = "/start-recording"
        request.scope["raw_path"] = b"/start-recording"
    if (request.method == "GET" and path in ("/", "/recording")
            and server._accepts_html(request.headers.get("accept", ""))):
        # The same SPA shell includes the existing Chromium Astra snapshot workaround.
        response = await asyncio.to_thread(server.calibration_spa_page)
    elif start or discovery or preview or path == "/camera-preview-stop":
        async with camera_handoff:
            if record.recording_active and discovery:
                config = record.recording_config
                cameras = [
                    {"index": camera["camera_index"], "name": name, "available": True}
                    for name, camera in (config.cameras if config else {}).items()
                    if "camera_index" in camera
                ]
                response = JSONResponse({"status": "success", "cameras": cameras})
            elif record.recording_active and path.startswith("/camera-preview/") and preview:
                config = record.recording_config
                index = path.rsplit("/", 1)[1]
                key = next((name for name, cam in (config.cameras if config else {}).items()
                            if str(cam.get("camera_index")) == index), None)
                if key is None:
                    response = JSONResponse({"detail": "Camera outside this recording"}, status_code=409)
                else:
                    request.scope["path"] = f"/camera-feed/{key}"
                    request.scope["raw_path"] = request.scope["path"].encode()
                    response = await call_next(request)
            elif record.recording_active and preview:
                response = await asyncio.to_thread(recording_snapshot, path.rsplit("/", 1)[1])
            elif start and not (record.recording_active or rollout.inference_active or teleoperate.teleoperation_active):
                try:
                    await asyncio.to_thread(server.stop_camera_previews)
                except HTTPException as exc:
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers["X-LeLab-Recording-Guard"] = "1"
    return response
