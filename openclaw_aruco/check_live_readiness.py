#!/usr/bin/env python3
"""Read-only LeLab/OpenClaw preflight. It never sends a robot command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


LELAB_BASE = "http://127.0.0.1:8022"
ENDPOINTS = (
    "health",
    "robots/so-101",
    "available-cameras",
    "teleoperation-status",
    "inference-status",
    "recording-status",
    "calibration-status",
)
EXPECTED_CAMERAS = {"ceiling_vertical", "ceiling_oblique", "end_effector"}


def fetch_json(endpoint: str) -> dict[str, Any]:
    if endpoint not in ENDPOINTS:
        raise ValueError("endpoint is not on the read-only allowlist")
    request = Request(f"{LELAB_BASE}/{endpoint}", method="GET")
    with urlopen(request, timeout=4) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("status response exceeds size limit")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise ValueError("status response is not an object")
    return document


def check_live_readiness(
    workspace_config: dict[str, Any],
    fk_diagnostic: dict[str, Any],
    fetch: Callable[[str], dict[str, Any]] = fetch_json,
) -> dict[str, Any]:
    """Report connectivity and hard blockers; never declare motion readiness."""
    responses: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for endpoint in ENDPOINTS:
        try:
            responses[endpoint] = fetch(endpoint)
        except (OSError, URLError, ValueError, KeyError, json.JSONDecodeError) as exc:
            errors[endpoint] = f"{type(exc).__name__}: {exc}"

    blocked: list[str] = []
    health = responses.get("health", {})
    site_connected = health.get("status") == "ok"
    if not site_connected:
        blocked.append("LeLab 8022 health check failed")
    for endpoint in ENDPOINTS:
        if endpoint in errors:
            blocked.append(f"read-only {endpoint} check failed")

    configured = responses.get("robots/so-101", {}).get("robot", {}).get("cameras", [])
    available = responses.get("available-cameras", {}).get("cameras", [])
    configured_by_name = {
        item.get("name"): item.get("camera_index")
        for item in configured if isinstance(item, dict)
    } if isinstance(configured, list) else {}
    available_indices = {
        item.get("index") for item in available
        if isinstance(item, dict) and item.get("available") is True
    } if isinstance(available, list) else set()
    missing_names = sorted(EXPECTED_CAMERAS - configured_by_name.keys())
    not_enumerated_devices = {
        name: configured_by_name[name]
        for name in sorted(EXPECTED_CAMERAS & configured_by_name.keys())
        if configured_by_name[name] not in available_indices
    }
    if missing_names:
        blocked.append(f"camera configuration missing: {missing_names}")
    if not_enumerated_devices:
        blocked.append(
            "camera presence/live feed unverified by available-cameras: "
            f"{not_enumerated_devices} (a live preview may hold the V4L2 device open)"
        )

    active_flags = {
        "teleoperation": responses.get("teleoperation-status", {}).get("teleoperation_active"),
        "inference": responses.get("inference-status", {}).get("inference_active"),
        "recording": responses.get("recording-status", {}).get("recording_active"),
        "calibration": responses.get("calibration-status", {}).get("calibration_active"),
    }
    for name, active in active_flags.items():
        if active is not False:
            blocked.append(f"{name} is active or its inactive status is unverified")

    teleop_safety = responses.get("teleoperation-status", {}).get("safety", {})
    body_start_limit = teleop_safety.get("body_start_max_delta_deg")
    if (
        not isinstance(body_start_limit, (int, float))
        or isinstance(body_start_limit, bool)
        or body_start_limit > 3.0
    ):
        blocked.append(
            "deployed teleoperation body start gate is looser than or unverified against the reviewed 3-degree gate"
        )

    mode = workspace_config.get("mode", {})
    if mode.get("robot_enabled") is not False or mode.get("motion_authorized") is not False:
        blocked.append("workspace config is not motion-disabled")
    blocked.extend(str(reason) for reason in workspace_config.get("motion_blocked_reasons", []))
    blocked.extend(str(reason) for reason in fk_diagnostic.get("blocking_reasons", []))

    return {
        "schema_version": 1,
        "status": "BLOCKED_FOR_MOTION",
        "site_connected": site_connected,
        "configured_cameras": configured_by_name,
        "enumerated_camera_indices": sorted(index for index in available_indices if isinstance(index, int)),
        "not_enumerated_camera_devices": not_enumerated_devices,
        "active_flags": active_flags,
        "teleoperation_body_start_max_delta_deg": body_start_limit,
        "endpoint_errors": errors,
        "blocked_reasons": list(dict.fromkeys(blocked)),
        "motion_authorized": False,
        "robot_commands_sent": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-config", required=True, type=Path)
    parser.add_argument("--fk-diagnostic", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error(f"output already exists: {args.output}")
    workspace_config = json.loads(args.workspace_config.read_text(encoding="utf-8"))
    fk_diagnostic = json.loads(args.fk_diagnostic.read_text(encoding="utf-8"))
    report = check_live_readiness(workspace_config, fk_diagnostic)
    result = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
