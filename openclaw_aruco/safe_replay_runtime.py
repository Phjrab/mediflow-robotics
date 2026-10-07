"""Hardware-agnostic safety runtime for one supervised recorded-path run.

This module deliberately contains no LeRobot, serial, motor, camera, or HTTP
imports.  A separately reviewed hardware adapter would have to provide the
sender callback after an explicit, short-lived user authorization record.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_authorization(
    authorization: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    now: float,
) -> None:
    if authorization.get("status") != "AUTHORIZED_SINGLE_SUPERVISED_RUN":
        raise ValueError("authorization status is not a single supervised run")
    if authorization.get("motion_authorized") is not True:
        raise ValueError("motion_authorized must be true in the separate authorization record")
    if plan.get("motion_authorized") is not False:
        raise ValueError("source plan must remain a motion-disabled dry-run artifact")
    if plan.get("status") != "PHYSICALLY_VALIDATED_SUPERVISED_PATH":
        raise ValueError("source plan is not a physically validated supervised path")
    if plan.get("blocking_reasons"):
        raise ValueError("source plan still has blocking reasons")
    safety = plan.get("physical_safety_validation", {})
    required = (
        "tcp", "robot_world_transform", "workspace", "joint_limits",
        "calibrated_action_range", "stop_path",
    )
    if any(safety.get(key) is not True for key in required):
        raise ValueError("physical safety validation is incomplete")
    integrity = plan.get("control", {}).get("source_action_integrity", {})
    if integrity.get("source_actions_modified") is not False:
        raise ValueError("source action integrity is not verified")
    if authorization.get("task") != plan.get("task"):
        raise ValueError("authorization task does not match the plan")
    if authorization.get("plan_sha256") != plan_sha256:
        raise ValueError("authorization does not match the exact plan file")
    if authorization.get("power_cutoff_ready") is not True:
        raise ValueError("power cutoff readiness was not confirmed")
    if authorization.get("user_confirmation") != "배치 유지, 전원차단 준비":
        raise ValueError("required user confirmation text is missing")
    nonce = authorization.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        raise ValueError("authorization nonce is missing or too short")
    expires_at = authorization.get("expires_at_unix")
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        raise ValueError("authorization expiry is invalid")
    if expires_at <= now or expires_at > now + 300:
        raise ValueError("authorization must expire within the next five minutes")
    max_run = authorization.get("max_run_seconds")
    if not isinstance(max_run, (int, float)) or isinstance(max_run, bool) or not 1 <= max_run <= 60:
        raise ValueError("authorized run limit must be between 1 and 60 seconds")


def run_with_safety_runtime(
    plan: dict[str, Any],
    authorization: dict[str, Any],
    plan_sha256: str,
    sender: Callable[[dict[str, float]], None],
    stop_path: Path,
    lock_path: Path,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run targets through a supplied callback with lock/stop/deadline gates."""

    validate_authorization(authorization, plan, plan_sha256, time.time())
    control_hz = float(plan["control"]["control_hz"])
    period = 1.0 / control_hz
    deadline_seconds = min(
        float(authorization["max_run_seconds"]),
        float(plan["control"]["estimated_duration_seconds"]) + 1.0,
    )
    targets = plan.get("targets")
    joints = plan.get("joints")
    if not isinstance(targets, list) or not targets or not isinstance(joints, list):
        raise ValueError("plan targets or joints are invalid")

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another replay run already holds the lock") from exc
        started = monotonic()
        sent = 0
        reason = "completed"
        for item in targets:
            if stop_path.exists():
                reason = "stop_requested"
                break
            if monotonic() - started >= deadline_seconds:
                reason = "deadline_reached"
                break
            values = item.get("target")
            if not isinstance(values, list) or len(values) != len(joints):
                raise ValueError("target shape does not match joint list")
            sender(dict(zip(joints, (float(value) for value in values))))
            sent += 1
            next_tick = started + sent * period
            delay = next_tick - monotonic()
            if delay > 0:
                sleeper(delay)
        return {
            "status": "RUN_FINISHED" if reason == "completed" else "RUN_STOPPED",
            "reason": reason,
            "targets_sent": sent,
            "motion_authorized": False,
            "authorization_consumed": True,
        }
