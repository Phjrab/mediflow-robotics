# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import contextlib
import glob
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

# Import our custom recording functionality
from . import datasets as dataset_browser, record as _record

# Import our custom calibration functionality
from .calibrate import CalibrationRequest, calibration_manager
from .jobs import (
    JobAlreadyRunningError,
    JobNotFoundError,
    JobNotRunningError,
    JobTarget,
    job_registry,
)
from .record import (
    DatasetInfoRequest,
    RecordingRequest,
    UploadRequest,
    handle_delete_dataset,
    handle_exit_early,
    handle_get_dataset_info,
    handle_recording_status,
    handle_rerecord_episode,
    handle_start_recording,
    handle_stop_recording,
    handle_upload_dataset,
)
from .rollout import (
    InferenceRequest,
    handle_inference_status,
    handle_start_inference,
    handle_stop_inference,
)

# Import our custom teleoperation functionality
from .teleoperate import (
    TeleoperateRequest,
    handle_get_joint_positions,
    handle_start_teleoperation,
    handle_stop_teleoperation,
    handle_teleoperation_status,
)

# Training is now job-based; see app/jobs.py.
from .train import TrainingRequest
from .update import handle_run_update, handle_update_check
from .utils import config
from .utils.config import (
    FOLLOWER_CONFIG_PATH,
    LEADER_CONFIG_PATH,
    delete_robot_record,
    detect_port_after_disconnect,
    find_available_ports,
    find_robot_port,
    get_default_robot_port,
    get_robot_record,
    get_saved_robot_port,
    is_robot_record_clean,
    is_valid_robot_name,
    list_robot_records,
    save_robot_port,
    save_robot_record,
)
from .utils.hf_auth import cached_whoami, handle_hf_auth_status, handle_hf_login, shared_hf_api
from .utils.system import (
    handle_get_cuda_status,
    handle_get_policy_extra,
    handle_get_training_extra,
    handle_get_wandb_extra,
    handle_install_policy_extra,
    handle_install_policy_extra_status,
    handle_install_training_extra,
    handle_install_training_extra_status,
    handle_install_wandb_extra,
    handle_install_wandb_extra_status,
    warn_if_cuda_mismatch,
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StartTrainingBody(BaseModel):
    """Wrapping body for POST /jobs/training. Adds optional target spec."""

    config: TrainingRequest
    target: JobTarget | None = None

    @classmethod
    def from_legacy(cls, raw: dict) -> "StartTrainingBody":
        """Accept the old request shape (TrainingRequest fields at top level)
        as well as the new shape ({config: ..., target: ...}).
        """
        if "config" in raw and isinstance(raw["config"], dict):
            return cls.model_validate(raw)
        # Legacy: top-level training fields, no target.
        return cls(config=TrainingRequest.model_validate(raw))


# Cache for HF Jobs hardware flavors (5-minute TTL)
_flavors_cache: dict = {"data": None, "fetched_at": 0.0}
_FLAVOR_CACHE_TTL_SECONDS = 300.0


app = FastAPI()

# In dev mode the React app runs on :8080 while the API runs on :8000; in
# prod they share an origin and CORS is unnecessary. allow_credentials with
# a wildcard origin is rejected by browsers, so we drop it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

# Get the path to the lerobot root directory (3 levels up from this script)
LEROBOT_PATH = str(Path(__file__).parent.parent.parent.parent)
logger.info(f"LeRobot path: {LEROBOT_PATH}")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.broadcast_queue = queue.Queue()
        self.broadcast_thread = None
        self.is_running = False
        # Guards `active_connections` since the broadcast worker thread also
        # mutates it on send failure.
        self._connections_lock = threading.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._connections_lock:
            self.active_connections.append(websocket)
            count = len(self.active_connections)
        logger.info(f"WebSocket connected. Total connections: {count}")

        if not self.is_running:
            self.start_broadcast_thread()

    def disconnect(self, websocket: WebSocket):
        with self._connections_lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                count = len(self.active_connections)
                logger.info(f"WebSocket disconnected. Total connections: {count}")
            else:
                count = len(self.active_connections)

        if count == 0 and self.is_running:
            self.stop_broadcast_thread()

    def start_broadcast_thread(self):
        """Start the background thread for broadcasting data"""
        if self.is_running:
            return

        self.is_running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_worker, daemon=True)
        self.broadcast_thread.start()
        logger.info("📡 Broadcast thread started")

    def stop_broadcast_thread(self):
        """Stop the background thread"""
        self.is_running = False
        if self.broadcast_thread:
            self.broadcast_thread.join(timeout=1.0)
            logger.info("📡 Broadcast thread stopped")

    def _broadcast_worker(self):
        """Background worker thread for broadcasting WebSocket data"""
        import asyncio

        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            while self.is_running:
                try:
                    # Get data from queue with timeout
                    data = self.broadcast_queue.get(timeout=0.1)
                    if data is None:  # Poison pill to stop
                        break

                    # Broadcast to all connections
                    if self.active_connections:
                        loop.run_until_complete(self._send_to_all_connections(data))

                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error in broadcast worker: {e}")

        finally:
            loop.close()

    async def _send_to_all_connections(self, data: dict[str, Any]):
        """Send data to all active WebSocket connections"""
        with self._connections_lock:
            connections = list(self.active_connections)
        if not connections:
            return

        disconnected = []
        for connection in connections:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.error(f"Error sending data to WebSocket: {e}")
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)

    def broadcast_joint_data_sync(self, data: dict[str, Any]):
        """Thread-safe method to queue data for broadcasting"""
        if self.is_running and self.active_connections:
            try:
                self.broadcast_queue.put_nowait(data)
            except queue.Full:
                logger.warning("Broadcast queue is full, dropping data")

    def notify_jobs_changed(self) -> None:
        """Push a 'jobs_changed' event to all WS clients so they refetch.

        Called from JobRegistry on submit / watchdog finalisation / delete.
        Skipped silently if no clients are connected — the frontend does an
        initial fetch on mount, so a missed broadcast is self-healing.
        """
        if self.is_running and self.active_connections:
            with contextlib.suppress(queue.Full):
                self.broadcast_queue.put_nowait({"type": "jobs_changed", "timestamp": time.time()})

    def notify_job_progress(self, snapshots: list[dict]) -> None:
        """Push a 'job_progress' event with per-running-job snapshots.

        Fired from the JobRegistry watchdog (~1Hz) while jobs are running so
        the dashboard's progress bar updates live without refetching /jobs
        (let alone /jobs/hub, which hits the HF API on every call).
        """
        if self.is_running and self.active_connections:
            with contextlib.suppress(queue.Full):
                self.broadcast_queue.put_nowait(
                    {"type": "job_progress", "jobs": snapshots, "timestamp": time.time()}
                )


manager = ConnectionManager()
job_registry.set_on_change(manager.notify_jobs_changed)
job_registry.set_on_progress(manager.notify_job_progress)


@app.get("/get-configs")
def get_configs():
    # Get all available calibration configs
    leader_configs = [os.path.basename(f) for f in glob.glob(os.path.join(LEADER_CONFIG_PATH, "*.json"))]
    follower_configs = [os.path.basename(f) for f in glob.glob(os.path.join(FOLLOWER_CONFIG_PATH, "*.json"))]

    return {"leader_configs": leader_configs, "follower_configs": follower_configs}


@app.post("/move-arm")
def teleoperate_arm(request: TeleoperateRequest):
    """Start teleoperation of the robot arm"""
    return handle_start_teleoperation(request, manager)


@app.post("/stop-teleoperation")
def stop_teleoperation():
    """Stop the current teleoperation session"""
    return handle_stop_teleoperation()


@app.get("/teleoperation-status")
def teleoperation_status():
    """Get the current teleoperation status"""
    return handle_teleoperation_status()


@app.get("/joint-positions")
def get_joint_positions():
    """Get current robot joint positions"""
    return handle_get_joint_positions()


@app.post("/start-inference")
def start_inference(request: InferenceRequest):
    result = handle_start_inference(request)
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to start inference"),
        )
    return result


@app.post("/stop-inference")
def stop_inference():
    result = handle_stop_inference()
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to stop inference"),
        )
    return result


@app.get("/inference-status")
def inference_status():
    return handle_inference_status()


@app.get("/health")
def health_check():
    """Simple health check endpoint to verify server is running"""
    return {"status": "ok", "message": "FastAPI server is running"}


@app.get("/hf-auth-status")
def hf_auth_status():
    """Check whether the local HF CLI is authenticated and return user info."""
    return handle_hf_auth_status()


class HfLoginBody(BaseModel):
    token: str


@app.post("/hf-auth/login")
def hf_auth_login(body: HfLoginBody):
    """Persist a pasted HF token (validated against whoami) for this user."""
    try:
        return handle_hf_login(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/datasets")
def datasets_list():
    """List datasets available to the user — Hub-owned + local cache.

    Each entry carries a `source` field: "local", "hub", or "both".
    """
    return dataset_browser.list_all_datasets()


@app.get("/ws-test")
def websocket_test():
    """Test endpoint to verify WebSocket support"""
    return {"websocket_endpoint": "/ws/joint-data", "status": "available"}


@app.websocket("/ws/joint-data")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("🔗 New WebSocket connection attempt")
    try:
        await manager.connect(websocket)
        logger.info("✅ WebSocket connection established")

        while True:
            # Keep the connection alive and wait for messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                # Handle any incoming messages if needed
                logger.debug(f"Received WebSocket message: {data}")
            except asyncio.TimeoutError:
                # No message received, continue
                pass
            except WebSocketDisconnect:
                logger.info("🔌 WebSocket client disconnected")
                break

            # Small delay to prevent excessive CPU usage
            await asyncio.sleep(0.01)

    except WebSocketDisconnect:
        logger.info("🔌 WebSocket disconnected normally")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)
        logger.info("🧹 WebSocket connection cleaned up")


@app.post("/start-recording")
def start_recording(request: RecordingRequest):
    """Start a dataset recording session"""
    return handle_start_recording(request)


@app.post("/stop-recording")
def stop_recording():
    """Stop the current recording session"""
    return handle_stop_recording()


@app.get("/recording-status")
def recording_status():
    """Get the current recording status"""
    return handle_recording_status()


@app.get("/camera-feed/{cam_key}")
def camera_feed(cam_key: str):
    """Live MJPEG preview of one configured camera during an active recording.

    Browsers render `multipart/x-mixed-replace` directly in an <img>, so the
    frontend just points an <img> at this URL. Only valid while a session is
    active; the generator ends itself when recording stops.
    """
    if not _record.recording_active:
        raise HTTPException(status_code=409, detail="No active recording session")
    return StreamingResponse(
        _record.camera_feed_frames(cam_key),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/recording-exit-early")
def recording_exit_early():
    """Skip to next episode (replaces right arrow key)"""
    return handle_exit_early()


@app.post("/recording-rerecord-episode")
def recording_rerecord_episode():
    """Re-record current episode (replaces left arrow key)"""
    return handle_rerecord_episode()


@app.post("/upload-dataset")
def upload_dataset(request: UploadRequest):
    """Upload dataset to HuggingFace Hub"""
    return handle_upload_dataset(request)


@app.post("/dataset-info")
def get_dataset_info(request: DatasetInfoRequest):
    """Get information about a saved dataset"""
    return handle_get_dataset_info(request)


@app.post("/delete-dataset")
def delete_dataset(request: DatasetInfoRequest):
    """Remove a recorded dataset directory from local disk."""
    return handle_delete_dataset(request)


# ============================================================================
# JOB ENDPOINTS
# ============================================================================


@app.post("/jobs/training", status_code=201)
async def create_training_job(req: Request):
    raw = await req.json()
    body = StartTrainingBody.from_legacy(raw)
    try:
        record = job_registry.start(body.config, body.target)
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job already running: {exc}") from exc
    except ValueError as exc:
        # e.g. "flavor is required when runner is hf_cloud"
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record


class ImportModelRequest(BaseModel):
    source: str
    name: str | None = None


@app.post("/jobs/import", status_code=201)
def import_model(body: ImportModelRequest):
    """Register an external model (local dir or HF repo) as a pseudo-job."""
    try:
        return job_registry.register_imported(body.source, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/jobs")
def list_jobs(limit: int = 10):
    return {"jobs": job_registry.list(limit=limit)}


@app.get("/jobs/hub")
def list_hub_jobs():
    """List the user's HF Cloud compute Jobs and their uploaded LeRobot model
    repos on huggingface.co.

    Returns 200 with empty lists when no token is configured so the frontend
    can render an unauthenticated empty state without surfacing an error.

    Declared before `/jobs/{job_id}` so FastAPI's first-match routing doesn't
    treat "hub" as a job id.
    """
    info = cached_whoami()
    if info is None:
        return {"authenticated": False, "jobs": [], "models": []}
    api = shared_hf_api()

    authors: list[str] = []
    if info.get("name"):
        authors.append(info["name"])
    for o in info.get("orgs", []) or []:
        if isinstance(o, dict) and o.get("name"):
            authors.append(o["name"])

    try:
        jobs = api.list_jobs()
    except Exception as exc:
        logger.warning("list_jobs failed: %s", exc)
        jobs = []

    seen_models: set[str] = set()
    models: list[dict] = []
    for author in authors:
        try:
            for m in api.list_models(author=author, filter="LeRobot", limit=200):
                if m.id in seen_models:
                    continue
                seen_models.add(m.id)
                models.append(
                    {
                        "repo_id": m.id,
                        "last_modified": m.last_modified.isoformat() if m.last_modified else None,
                        "private": bool(getattr(m, "private", False)),
                    }
                )
        except Exception as exc:
            logger.warning("list_models(%s) failed: %s", author, exc)
    models.sort(key=lambda m: m["last_modified"] or "", reverse=True)

    return {
        "authenticated": True,
        "jobs": [
            {
                "id": ji.id,
                "created_at": ji.created_at.isoformat() if ji.created_at else None,
                "docker_image": ji.docker_image,
                "space_id": ji.space_id,
                "flavor": ji.flavor,
                "status": ({"stage": ji.status.stage, "message": ji.status.message} if ji.status else None),
                "owner": ji.owner.name if ji.owner else None,
                "url": ji.url,
            }
            for ji in jobs
        ],
        "models": models,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return job_registry.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc


@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    try:
        logs = job_registry.drain_logs(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    return {"logs": logs}


@app.get("/jobs/{job_id}/log-file")
def get_job_log_file(job_id: str):
    """Return the entire on-disk log file for a job. Drains the live queue too
    so the next /logs poll returns only lines that arrived after this call."""
    try:
        logs = job_registry.read_persisted_logs(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    # Best-effort drain so the frontend doesn't double-display.
    with contextlib.suppress(JobNotFoundError):
        job_registry.drain_logs(job_id)
    return {"logs": logs}


@app.get("/jobs/{job_id}/metrics-history")
def get_job_metrics_history(job_id: str):
    """Return the per-step loss/lr/grad-norm series reconstructed from the
    job's log.jsonl. Used to seed the monitoring charts so curves persist
    across page reloads, navigation, and lelab restarts."""
    try:
        points = job_registry.read_metrics_history(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    return {"points": points}


@app.get("/jobs/{job_id}/checkpoints")
def get_job_checkpoints(job_id: str):
    """List the checkpoints saved for this job, ascending by step."""
    try:
        return {"checkpoints": job_registry.list_checkpoints(job_id)}
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc


@app.get("/jobs/{job_id}/checkpoints/{step}/policy-config")
def get_checkpoint_policy_config(job_id: str, step: int):
    """Return the UX-relevant slice of a checkpoint's pretrained_model config:
    policy_type, image_features (per-camera height/width), and requires_task."""
    try:
        return job_registry.get_policy_config_summary(job_id, step)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/jobs/{job_id}/stop")
def stop_job(job_id: str):
    try:
        return job_registry.stop(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except JobNotRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is not running") from exc


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        job_registry.delete(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found") from exc
    except JobNotRunningError as exc:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is running; stop it first") from exc


@app.get("/jobs/runners/hardware")
def get_runners_hardware():
    """Return HF Jobs flavor catalog + auth state for the TargetCard.

    Both the flavors list and the whoami result are cached in-process to
    keep this endpoint cheap (it can be re-fetched whenever auth state
    changes). The whoami cache is invalidated on login.
    """
    info = cached_whoami()
    if info is None or not info.get("name"):
        return {"authenticated": False, "username": None, "flavors": []}
    username: str = info["name"]
    api = shared_hf_api()

    now = time.time()
    if _flavors_cache["data"] is None or now - _flavors_cache["fetched_at"] > _FLAVOR_CACHE_TTL_SECONDS:
        try:
            hw_list = api.list_jobs_hardware()
        except Exception as exc:
            logger.warning("list_jobs_hardware failed: %s", exc)
            return {"authenticated": True, "username": username, "flavors": []}
        _flavors_cache["data"] = [
            {
                "name": h.name,
                "pretty_name": h.pretty_name,
                "cpu": h.cpu,
                "ram": h.ram,
                "accelerator": h.accelerator,
                "unit_cost_usd": h.unit_cost_usd,
                "unit_label": h.unit_label,
            }
            for h in hw_list
        ]
        _flavors_cache["fetched_at"] = now

    return {
        "authenticated": True,
        "username": username,
        "flavors": _flavors_cache["data"],
    }


# ============================================================================
# SYSTEM ENDPOINTS
# ============================================================================


@app.get("/system/cuda-status")
def get_cuda_status():
    """Report whether an NVIDIA GPU is present but PyTorch is CPU-only (issue #30)."""
    return handle_get_cuda_status()


@app.get("/system/training-extra")
def get_training_extra():
    """Return whether the LeRobot training extra (accelerate) is importable."""
    return handle_get_training_extra()


@app.post("/system/training-extra/install")
def install_training_extra():
    """Spawn `pip install accelerate` as a background subprocess. No-op if already running."""
    return handle_install_training_extra()


@app.get("/system/training-extra/install-status")
def install_training_extra_status():
    """Return current install state plus any pending log lines (drained on read)."""
    return handle_install_training_extra_status()


@app.get("/system/wandb-extra")
def get_wandb_extra():
    """Return whether the `wandb` package is importable in this lelab process."""
    return handle_get_wandb_extra()


@app.post("/system/wandb-extra/install")
def install_wandb_extra():
    """Spawn `pip install wandb` as a background subprocess. No-op if already running."""
    return handle_install_wandb_extra()


@app.get("/system/wandb-extra/install-status")
def install_wandb_extra_status():
    """Return current wandb install state plus any pending log lines (drained on read)."""
    return handle_install_wandb_extra_status()


@app.get("/system/policy-extra/{policy_type}")
def get_policy_extra(policy_type: str):
    """Whether the optional LeRobot extra a policy needs (e.g. transformers for
    smolvla/pi0, diffusers for diffusion) is importable. Core policies report available."""
    return handle_get_policy_extra(policy_type)


@app.post("/system/policy-extra/{policy_type}/install")
def install_policy_extra(policy_type: str):
    """Spawn `pip install lerobot[<extra>]` for the policy's extra in the background."""
    return handle_install_policy_extra(policy_type)


@app.get("/system/policy-extra/{policy_type}/install-status")
def install_policy_extra_status(policy_type: str):
    """Return the policy extra's install state plus any pending log lines (drained on read)."""
    return handle_install_policy_extra_status(policy_type)


@app.get("/system/update-check")
def update_check():
    """Report whether a newer LeLab commit exists on GitHub (cached, silent on failure)."""
    return handle_update_check()


@app.post("/system/update")
def run_update():
    """Run the pip upgrade in-process; the user must restart lelab afterwards."""
    return handle_run_update()


# Replay is rendered by the embedded lerobot/visualize_dataset Space; no backend routes needed.


# ============================================================================
# Calibration endpoints
@app.post("/start-calibration")
def start_calibration(request: CalibrationRequest):
    """Start calibration process"""
    return calibration_manager.start_calibration(request)


@app.post("/stop-calibration")
def stop_calibration():
    """Stop calibration process"""
    return calibration_manager.stop_calibration_process()


@app.get("/calibration-status")
def calibration_status():
    """Get current calibration status"""
    from dataclasses import asdict

    status = calibration_manager.get_status()
    return asdict(status)


@app.post("/complete-calibration-step")
def complete_calibration_step():
    """Complete the current calibration step"""
    return calibration_manager.complete_step()


@app.get("/calibration-configs/{device_type}")
def get_calibration_configs(device_type: str):
    """Get all calibration config files for a specific device type"""
    try:
        if device_type == "robot":
            config_path = FOLLOWER_CONFIG_PATH
        elif device_type == "teleop":
            config_path = LEADER_CONFIG_PATH
        else:
            return {"success": False, "message": "Invalid device type"}

        # Get all JSON files in the config directory
        configs = []
        if os.path.exists(config_path):
            for file in os.listdir(config_path):
                if file.endswith(".json"):
                    config_name = os.path.splitext(file)[0]
                    file_path = os.path.join(config_path, file)
                    file_size = os.path.getsize(file_path)
                    modified_time = os.path.getmtime(file_path)

                    configs.append(
                        {
                            "name": config_name,
                            "filename": file,
                            "size": file_size,
                            "modified": modified_time,
                        }
                    )

        return {"success": True, "configs": configs, "device_type": device_type}

    except Exception as e:
        logger.error(f"Error getting calibration configs: {e}")
        return {"success": False, "message": str(e)}


@app.delete("/calibration-configs/{device_type}/{config_name}")
def delete_calibration_config(device_type: str, config_name: str):
    """Delete a calibration config file"""
    try:
        if device_type == "robot":
            config_path = FOLLOWER_CONFIG_PATH
        elif device_type == "teleop":
            config_path = LEADER_CONFIG_PATH
        else:
            return {"success": False, "message": "Invalid device type"}

        # config_name is interpolated into a filename, so reject path-traversal
        # characters (/, \, ..) before touching the filesystem. Defense-in-depth:
        # FastAPI path params already block a literal "/", but not "\" or "..".
        # Reuses the same guard already applied to robot-record deletes.
        if not is_valid_robot_name(config_name):
            return {"success": False, "message": "Invalid configuration name"}

        # Construct the file path
        filename = f"{config_name}.json"
        file_path = os.path.join(config_path, filename)

        # Check if file exists
        if not os.path.exists(file_path):
            return {"success": False, "message": "Configuration file not found"}

        # Delete the file
        os.remove(file_path)
        logger.info(f"Deleted calibration config: {file_path}")

        return {
            "success": True,
            "message": f"Configuration '{config_name}' deleted successfully",
        }

    except Exception as e:
        logger.error(f"Error deleting calibration config: {e}")
        return {"success": False, "message": str(e)}


# ============================================================================
# PORT DETECTION ENDPOINTS
# ============================================================================


@app.get("/available-ports")
def get_available_ports():
    """Get all available serial ports"""
    try:
        ports = find_available_ports()
        return {"status": "success", "ports": ports}
    except Exception as e:
        logger.error(f"Error getting available ports: {e}")
        return {"status": "error", "message": str(e)}


# Runs in a fresh Python — see _avfoundation_cameras_in_cv2_order for why.
# Mirrors OpenCV's macOS enumeration: video + muxed devices sorted by
# uniqueID (cap_avfoundation_mac.mm), so the returned index matches what
# cv2.VideoCapture will open.
_AVF_ENUM_SCRIPT = """
import json, objc
from Foundation import NSBundle
bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/AVFoundation.framework")
bundle.load()
types = []
for name in (
    "AVCaptureDeviceTypeBuiltInWideAngleCamera",
    "AVCaptureDeviceTypeExternalUnknown",   # macOS < 14
    "AVCaptureDeviceTypeExternal",          # macOS >= 14
    "AVCaptureDeviceTypeContinuityCamera",  # macOS >= 14
    "AVCaptureDeviceTypeDeskViewCamera",    # macOS >= 13
):
    loaded = {}
    try:
        objc.loadBundleVariables(bundle, loaded, [(name, b"@")])
    except objc.error:
        continue
    if loaded.get(name) is not None:
        types.append(loaded[name])
cls = objc.lookUpClass("AVCaptureDeviceDiscoverySession")
devs = []
for mt in ("vide", "muxx"):
    devs.extend(cls.discoverySessionWithDeviceTypes_mediaType_position_(types, mt, 0).devices() or [])
devs.sort(key=lambda d: d.uniqueID())
print(json.dumps([
    {"index": i, "name": str(d.localizedName()), "unique_id": str(d.uniqueID())}
    for i, d in enumerate(devs)
]))
"""


def _avfoundation_cameras_in_cv2_order() -> list[dict[str, Any]]:
    """Enumerate macOS cameras in a fresh Python subprocess.

    AVFoundation's in-process device cache doesn't refresh on USB
    hotplug. Both the deprecated ``+devicesWithMediaType:`` and a
    long-lived ``AVCaptureDeviceDiscoverySession`` go stale, because
    device-connection notifications are delivered via
    ``NSNotificationCenter`` on a thread that needs an active
    ``NSRunLoop`` — uvicorn workers don't run one. A fresh subprocess
    re-initializes AVFoundation, which reads IOKit's live device state
    at startup.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _AVF_ENUM_SCRIPT],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("AVFoundation enumeration subprocess failed: %s", e)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning("AVFoundation enumeration returned invalid JSON: %s", e)
        return []


def _generic_cv2_cameras(backend) -> list[dict[str, Any]]:
    """Last-resort enumeration: probe cv2 indices with placeholder names."""
    import cv2

    cameras: list[dict[str, Any]] = []
    for i in range(10):
        cap = cv2.VideoCapture(i, backend)
        opened = cap.isOpened()
        cap.release()
        if opened:
            cameras.append({"index": i, "name": f"Camera {i}", "available": True})
    return cameras


def _windows_cameras() -> list[dict[str, Any]]:
    """Enumerate Windows cameras with their real DirectShow names.

    pygrabber lists DirectShow video devices in the same order cv2's DSHOW
    backend indexes them (which recording is pinned to), so the returned index
    matches what ``cv2.VideoCapture(i, CAP_DSHOW)`` opens. The real names let the
    frontend match each index to the browser's ``MediaDeviceInfo.label`` for the
    live preview. Falls back to generic names if pygrabber is unavailable.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
    except Exception as e:  # ImportError, or a COM/DirectShow failure
        logger.warning("pygrabber unavailable; using generic camera names: %s", e)
        import cv2

        return _generic_cv2_cameras(cv2.CAP_DSHOW)
    return [{"index": i, "name": name, "available": True} for i, name in enumerate(names)]


def _v4l2_camera_name(index: int) -> str | None:
    """Real camera name for /dev/video{index} from sysfs (Linux, no deps)."""
    try:
        with open(f"/sys/class/video4linux/video{index}/name", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _linux_cameras() -> list[dict[str, Any]]:
    """Enumerate Linux cameras, naming each from sysfs (no extra deps)."""
    import cv2

    cameras: list[dict[str, Any]] = []
    for i in range(10):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        opened = cap.isOpened()
        cap.release()
        if not opened:
            continue
        cameras.append({"index": i, "name": _v4l2_camera_name(i) or f"Camera {i}", "available": True})
    return cameras


@app.get("/available-cameras")
def get_available_cameras():
    """List cameras with the same index ordering cv2 will use to record.

    Each platform enumerates in the order its cv2 backend indexes devices, and
    pairs each index with the device's real name so the frontend can match it to
    the browser's ``MediaDeviceInfo.label`` for the live preview:
      - macOS: AVFoundation ``localizedName`` (via a PyObjC subprocess);
      - Windows: DirectShow FriendlyName (via pygrabber; recording pinned DSHOW);
      - Linux: the v4l2 device name from sysfs.
    Without real names the frontend can't match a camera and shows "No browser
    match" with an empty device_id (issues #12, #16).
    """
    try:
        import platform

        system = platform.system()

        if system == "Darwin":
            cameras = _avfoundation_cameras_in_cv2_order()
            for cam in cameras:
                cam["available"] = True
            return {"status": "success", "cameras": cameras}
        if system == "Windows":
            return {"status": "success", "cameras": _windows_cameras()}
        if system == "Linux":
            return {"status": "success", "cameras": _linux_cameras()}

        import cv2

        return {"status": "success", "cameras": _generic_cv2_cameras(cv2.CAP_ANY)}
    except ImportError:
        logger.warning("OpenCV not available for camera detection")
        return {"status": "success", "cameras": []}
    except Exception as e:
        logger.error(f"Error detecting cameras: {e}")
        return {"status": "error", "message": str(e), "cameras": []}


_camera_preview_guard = threading.Lock()
_camera_preview_stops: dict[int, threading.Event] = {}
_camera_preview_caps: dict[int, Any] = {}
_astra_preview_sessions: dict[int, tuple[threading.Event, Any]] = {}
_astra_snapshot_condition = threading.Condition()
_astra_snapshot_payload: bytes | None = None
_astra_snapshot_sequence = 0
_astra_snapshot_thread: threading.Thread | None = None
_astra_snapshot_stop = threading.Event()


def _astra_snapshot_loop(width: int, height: int, fps: int) -> None:
    """Continuously capture Astra frames for browser snapshot polling.

    Chromium on the Jetson client can stop painting this V4L2-loopback MJPEG
    stream after a handful of frames even though the HTTP stream remains live.
    Keeping capture independent from the response and serving one complete JPEG
    per request avoids that decoder/back-pressure failure.
    """
    import cv2

    global _astra_snapshot_payload, _astra_snapshot_sequence

    while not _astra_snapshot_stop.is_set():
        cap = cv2.VideoCapture(8, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        if not cap.isOpened():
            cap.release()
            _astra_snapshot_stop.wait(0.5)
            continue
        try:
            while not _astra_snapshot_stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                with _astra_snapshot_condition:
                    next_sequence = _astra_snapshot_sequence + 1
                cv2.putText(
                    frame,
                    f"LIVE {next_sequence:06d}",
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                # Calibration-page-only alignment guide derived from the
                # recorded ACT-C episode-0 start frame (640x480).  These marks
                # are drawn only into /camera-snapshot/8 and never enter a
                # recording, training sample, or policy observation.
                sx = frame.shape[1] / 640.0
                sy = frame.shape[0] / 480.0

                def guide_box(x1: int, y1: int, x2: int, y2: int, color, label: str) -> None:
                    p1 = (round(x1 * sx), round(y1 * sy))
                    p2 = (round(x2 * sx), round(y2 * sy))
                    cv2.rectangle(frame, p1, p2, color, 2, cv2.LINE_AA)
                    cv2.putText(
                        frame,
                        label,
                        (p1[0], max(18, p1[1] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.42,
                        color,
                        1,
                        cv2.LINE_AA,
                    )

                # User-confirmed identity mapping: A is the largest bottle,
                # B is medium, and C is the smallest. In this recorded camera
                # layout the columns are C/B/A, not A/B/C.
                guide_box(300, 215, 333, 272, (255, 255, 255), "C-SMALL")
                guide_box(367, 215, 402, 272, (255, 255, 255), "B-MID")
                guide_box(429, 215, 465, 272, (255, 255, 255), "A-LARGE")
                # Use size identities only. Earlier color-based A/C mapping
                # was reversed and must not be used for placement.
                guide_box(278, 266, 349, 371, (0, 255, 255), "C-BIN")
                guide_box(350, 266, 417, 371, (0, 255, 255), "B-BIN")
                guide_box(418, 266, 484, 371, (0, 255, 255), "A-BIN")
                arm_target = (round(390 * sx), round(177 * sy))
                cv2.drawMarker(
                    frame,
                    arm_target,
                    (255, 255, 0),
                    markerType=cv2.MARKER_CROSS,
                    markerSize=18,
                    thickness=2,
                )
                cv2.putText(
                    frame,
                    "ARM START",
                    (arm_target[0] + 8, arm_target[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (255, 255, 0),
                    1,
                    cv2.LINE_AA,
                )
                encoded, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not encoded:
                    continue
                with _astra_snapshot_condition:
                    _astra_snapshot_sequence = next_sequence
                    _astra_snapshot_payload = jpeg.tobytes()
                    _astra_snapshot_condition.notify_all()
        finally:
            cap.release()
        _astra_snapshot_stop.wait(0.2)


def _ensure_astra_snapshot_thread(width: int, height: int, fps: int) -> None:
    global _astra_snapshot_thread

    with _astra_snapshot_condition:
        if _astra_snapshot_thread is not None and _astra_snapshot_thread.is_alive():
            return
        _astra_snapshot_stop.clear()
        _astra_snapshot_thread = threading.Thread(
            target=_astra_snapshot_loop,
            args=(width, height, fps),
            name="astra-snapshot-preview",
            daemon=True,
        )
        _astra_snapshot_thread.start()


@app.get("/camera-snapshot/8")
def astra_camera_snapshot(width: int = 640, height: int = 480, fps: int = 30):
    """Return one fresh Astra JPEG for the calibration-page polling preview."""
    width = min(max(width, 160), 3840)
    height = min(max(height, 120), 2160)
    fps = min(max(fps, 1), 60)
    _ensure_astra_snapshot_thread(width, height, fps)
    with _astra_snapshot_condition:
        if _astra_snapshot_payload is None:
            _astra_snapshot_condition.wait(timeout=2.0)
        payload = _astra_snapshot_payload
        sequence = _astra_snapshot_sequence
    if payload is None:
        raise HTTPException(status_code=503, detail="Astra camera is unavailable")
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Frame-Sequence": str(sequence),
        },
    )


@app.get("/camera-preview/{camera_index}")
def camera_preview(camera_index: int, width: int = 640, height: int = 480, fps: int = 30):
    """Stream a camera attached to the LeLab host for remote configuration UI."""
    import platform

    import cv2

    if camera_index < 0 or camera_index > 99:
        raise HTTPException(status_code=400, detail="Invalid camera index")
    width = min(max(width, 160), 3840)
    height = min(max(height, 120), 2160)
    fps = min(max(fps, 1), 60)

    backend = {
        "Linux": cv2.CAP_V4L2,
        "Windows": cv2.CAP_DSHOW,
        "Darwin": cv2.CAP_AVFOUNDATION,
    }.get(platform.system(), cv2.CAP_ANY)
    manual_yuyv = platform.system() == "Linux" and camera_index == 4

    is_astra_loopback = platform.system() == "Linux" and camera_index == 8

    # A browser reload can start a replacement MJPEG request while the old
    # generator is blocked inside cap.read(). Merely setting its stop event is
    # insufficient in that state: the stale VideoCapture keeps ownership of a
    # V4L2 loopback device (notably Astra /dev/video8), and the replacement
    # stream displays one cached frame or no frames. Release the old handle
    # before opening its replacement. Keep this section serialized so two
    # simultaneous React/image requests cannot race and both own a physical
    # camera. Astra is the exception: Chromium/React can retain a preload and
    # a visible <img> request concurrently, and /dev/video8 is a V4L2 loopback
    # that supports multiple readers. Replacing one Astra request with the
    # other stopped the visible stream after about 14 frames.
    with _camera_preview_guard:
        if not is_astra_loopback:
            previous_stop = _camera_preview_stops.pop(camera_index, None)
            previous_cap = _camera_preview_caps.pop(camera_index, None)
            if previous_stop is not None:
                previous_stop.set()
            if previous_cap is not None:
                previous_cap.release()

        if manual_yuyv:
            # Jetson's OpenCV/V4L2 conversion path currently turns this
            # RealSense RGB stream into a solid-green BGR image. Read packed
            # YUYV unchanged and perform the conversion explicitly instead.
            params = [
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"YUYV"),
                cv2.CAP_PROP_FRAME_WIDTH,
                width,
                cv2.CAP_PROP_FRAME_HEIGHT,
                height,
                cv2.CAP_PROP_FPS,
                fps,
                cv2.CAP_PROP_CONVERT_RGB,
                0,
            ]
            cap = cv2.VideoCapture(camera_index, backend, params)
        else:
            cap = cv2.VideoCapture(camera_index, backend)
            if platform.system() == "Linux":
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, fps)
        if not cap.isOpened():
            cap.release()
            raise HTTPException(status_code=503, detail="Camera is unavailable or busy")

        stop_event = threading.Event()
        astra_session_id = id(stop_event)
        if is_astra_loopback:
            _astra_preview_sessions[astra_session_id] = (stop_event, cap)
        else:
            _camera_preview_stops[camera_index] = stop_event
            _camera_preview_caps[camera_index] = cap

    def frames():
        transient_failures = 0
        frame_sequence = 0
        try:
            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    if is_astra_loopback:
                        logger.warning(
                            "Astra preview session %s read failed after %s frames",
                            astra_session_id,
                            frame_sequence,
                        )
                    # RealSense /dev/video4 can return a few empty frames while
                    # its raw YUYV stream is warming up. Do not permanently
                    # blank the preview because of that transient startup.
                    if manual_yuyv and transient_failures < 30:
                        transient_failures += 1
                        time.sleep(0.1)
                        continue
                    break
                transient_failures = 0
                if manual_yuyv and frame.size == width * height * 2:
                    packed = frame.reshape(height, width, 2)
                    frame = cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_YUY2)
                frame_sequence += 1
                if camera_index == 8:
                    if frame_sequence <= 10 or frame_sequence % 30 == 0:
                        logger.info(
                            "Astra preview session %s encoded frame %s",
                            astra_session_id,
                            frame_sequence,
                        )
                    # Preview-only liveness indicator for the Astra bridge.
                    # This frame never enters recording or policy inference;
                    # it only makes a stalled browser render obvious.
                    cv2.putText(
                        frame,
                        f"LIVE {frame_sequence:06d}",
                        (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 255, 0),
                        2,
                        cv2.LINE_AA,
                    )
                encoded, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not encoded:
                    continue
                payload = jpeg.tobytes()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                    + payload
                    + b"\r\n"
                )
        finally:
            if is_astra_loopback:
                logger.info(
                    "Astra preview session %s closed after %s frames (stop=%s)",
                    astra_session_id,
                    frame_sequence,
                    stop_event.is_set(),
                )
            cap.release()
            with _camera_preview_guard:
                if is_astra_loopback:
                    _astra_preview_sessions.pop(astra_session_id, None)
                elif _camera_preview_stops.get(camera_index) is stop_event:
                    _camera_preview_stops.pop(camera_index, None)
                    _camera_preview_caps.pop(camera_index, None)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/camera-preview-stop")
def stop_camera_previews():
    """Release every configuration preview before teleop or recording starts."""
    global _astra_snapshot_payload, _astra_snapshot_thread

    _astra_snapshot_stop.set()
    with _camera_preview_guard:
        active = list(_camera_preview_stops.values())
        captures = list(_camera_preview_caps.values())
        for stop_event, cap in _astra_preview_sessions.values():
            active.append(stop_event)
            captures.append(cap)
        _camera_preview_stops.clear()
        _camera_preview_caps.clear()
        _astra_preview_sessions.clear()
    for stop_event in active:
        stop_event.set()
    for cap in captures:
        cap.release()
    snapshot_thread = _astra_snapshot_thread
    if snapshot_thread is not None and snapshot_thread.is_alive():
        snapshot_thread.join(timeout=2.0)
    with _astra_snapshot_condition:
        _astra_snapshot_thread = None
        _astra_snapshot_payload = None
    return {"status": "success", "stopping": len(active)}


RobotSideLiteral = Literal["leader", "follower"]


class PortDetectionBody(BaseModel):
    robot_type: RobotSideLiteral = "follower"


class PortDisconnectBody(BaseModel):
    ports_before: list[str]


class SaveRobotPortBody(BaseModel):
    robot_type: RobotSideLiteral
    port: str


class SaveRobotConfigBody(BaseModel):
    robot_type: RobotSideLiteral
    config_name: str


@app.post("/start-port-detection")
def start_port_detection(body: PortDetectionBody):
    """Snapshot available ports so the follow-up /detect-port-after-disconnect
    call can diff them."""
    result = find_robot_port(body.robot_type)
    return {"status": "success", "data": result}


@app.post("/detect-port-after-disconnect")
def detect_port_after_disconnect_endpoint(body: PortDisconnectBody):
    """Block up to 15s waiting for one port from `ports_before` to disappear."""
    try:
        detected_port = detect_port_after_disconnect(body.ports_before)
    except OSError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    return {"status": "success", "port": detected_port}


@app.post("/save-robot-port")
def save_robot_port_endpoint(body: SaveRobotPortBody):
    """Save a robot port for future use"""
    save_robot_port(body.robot_type, body.port)
    return {"status": "success", "message": f"Port {body.port} saved for {body.robot_type}"}


@app.get("/robot-port/{robot_type}")
def get_robot_port(robot_type: RobotSideLiteral):
    """Get the saved port for a robot type"""
    saved_port = get_saved_robot_port(robot_type)
    default_port = get_default_robot_port(robot_type)
    return {"status": "success", "saved_port": saved_port, "default_port": default_port}


@app.post("/save-robot-config")
def save_robot_config_endpoint(body: SaveRobotConfigBody):
    """Save a robot configuration for future use"""
    if not config.save_robot_config(body.robot_type, body.config_name):
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    return {"status": "success", "message": f"Configuration saved for {body.robot_type}"}


@app.get("/robot-config/{robot_type}")
def get_robot_config(robot_type: RobotSideLiteral, available_configs: str = ""):
    """Get the saved configuration for a robot type"""
    available_configs_list = [c.strip() for c in available_configs.split(",") if c.strip()]
    saved_config = config.get_saved_robot_config(robot_type)
    default_config = config.get_default_robot_config(robot_type, available_configs_list)
    return {"status": "success", "saved_config": saved_config, "default_config": default_config}


# ============================================================================
# Robot config records (named robots)


def _record_with_clean(record: dict) -> dict:
    """Attach `is_clean` to a record for API responses."""
    return {**record, "is_clean": is_robot_record_clean(record)}


@app.get("/robots")
def get_robots():
    """List all saved robot records."""
    try:
        records = [_record_with_clean(r) for r in list_robot_records()]
        return {"status": "success", "robots": records}
    except Exception as e:
        logger.error(f"Error listing robots: {e}")
        return {"status": "error", "message": str(e), "robots": []}


@app.get("/robots/{name}")
def get_robot(name: str):
    """Get a single robot record by name."""
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    record = get_robot_record(name)
    if record is None:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Robot not found"})
    return {"status": "success", "robot": _record_with_clean(record)}


@app.post("/robots/{name}")
def upsert_robot(name: str, data: dict, create: bool = False):
    """
    Upsert a robot record.

    - `?create=true` is the "Add Robot" path: returns 409 if a record with that
      name already exists; otherwise creates with empty fields then merges body.
    - Without `?create=true` is the "patch" path (e.g., calibration write-back):
      merges body into existing record. If no record exists, no-ops and returns
      success — see deletion-during-calibration edge case in the spec.
    """
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    try:
        if create:
            if get_robot_record(name) is not None:
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "message": "A robot with this name already exists"},
                )
            save_robot_record(name, data or {}, allow_create=True)
        else:
            save_robot_record(name, data or {}, allow_create=False)
        record = get_robot_record(name)
        if record is None:
            return {"status": "success", "robot": None}
        return {"status": "success", "robot": _record_with_clean(record)}
    except Exception as e:
        logger.error(f"Error upserting robot {name}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.delete("/robots/{name}")
def delete_robot(name: str):
    """Delete a robot record."""
    if not is_valid_robot_name(name):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid robot name"})
    if delete_robot_record(name):
        return {"status": "success"}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Robot not found"})


@app.on_event("startup")
def startup_event():
    """One-time startup diagnostics surfaced in the server terminal."""
    warn_if_cuda_mismatch()


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources when FastAPI shuts down"""
    logger.info("🔄 FastAPI shutting down, cleaning up...")

    # Stop any active recording - handled by recording module cleanup

    if manager:
        manager.stop_broadcast_thread()
    logger.info("✅ Cleanup completed")


def _accepts_html(accept: str) -> bool:
    """Whether an Accept header explicitly wants text/html (quality > 0).

    Browser navigations list `text/html` with a positive quality value, so
    they get the SPA shell. A `text/html;q=0` entry is an explicit refusal and
    must not count — a plain substring check would wrongly treat it as a yes.
    `*/*` (curl, XHR, API clients) is deliberately not treated as wanting HTML.
    """
    for part in accept.split(","):
        media_type, _, params = part.strip().partition(";")
        if media_type.strip().lower() != "text/html":
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


_ASTRA_POLLING_SCRIPT = r"""
<script>
(() => {
  let timer = 0;
  const schedule = (delay) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(refreshAstra, delay);
  };
  const refreshAstra = () => {
    const img = document.querySelector('img[alt="ceiling_vertical preview"]');
    if (!img) {
      schedule(300);
      return;
    }
    const done = () => schedule(100);
    img.onload = done;
    img.onerror = () => schedule(500);
    img.src = `/camera-snapshot/8?width=640&height=480&fps=30&_=${Date.now()}`;
  };
  schedule(300);
})();
</script>
"""


@app.get("/calibration", include_in_schema=False)
def calibration_spa_page() -> Response:
    """Serve calibration with a polling Astra preview workaround.

    The other V4L2 cameras keep their normal MJPEG previews. Only the Astra
    loopback image is refreshed from complete JPEG responses because Chromium
    can freeze that MJPEG element after roughly 14 frames.
    """
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend is unavailable")
    html = index_path.read_text(encoding="utf-8")
    html = html.replace("</body>", f"{_ASTRA_POLLING_SCRIPT}</body>")
    return Response(
        content=html,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


class SPAStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unknown client-side routes.

    The frontend is a single-page app: routes like /recording and /calibration
    exist only in the browser's router, not as files on disk. A hard reload or
    deep link to one of those URLs asks the server for a file that isn't there;
    plain StaticFiles answers 404 ({"detail":"Not Found"}), so the page breaks.

    Here we fall back to index.html on 404 so the SPA boots and its router
    renders the route. Only requests that accept HTML (i.e. browser navigations)
    get the fallback — API typos, XHR, and curl still receive a JSON 404.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and _accepts_html(Headers(scope=scope).get("accept", "")):
                return await super().get_response("index.html", scope)
            raise


# Serve the built frontend at /. Must be mounted last so API routes win.
if FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning(
        f"frontend/dist not found at {FRONTEND_DIST}; run `npm run build` in frontend/ or use `lelab --dev`."
    )
