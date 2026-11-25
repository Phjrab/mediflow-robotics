"""Separate LeLab entry point. Only the recording module is overlaid."""
import asyncio
from pathlib import Path
import lelab

overlay = Path(__file__).resolve().parent/"overlay"
if not (overlay/"record.py").is_file():
    raise RuntimeError("Missing guarded recording module")
lelab.__path__.insert(0,str(overlay))
from lelab import record
from lelab.server import app

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
    if request.url.path == "/guarded-start-recording" and request.method == "POST":
        request.scope["path"] = "/start-recording"
        request.scope["raw_path"] = b"/start-recording"
    response = await call_next(request)
    response.headers["X-LeLab-Recording-Guard"] = "1"
    return response
