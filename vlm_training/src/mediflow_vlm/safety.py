from __future__ import annotations

from typing import Any

from .schema import validate_action_completion


def camera_family(camera: str | None) -> str:
    value = str(camera or "unknown").lower()
    for name in ("realsense", "astra", "video4", "video6"):
        if name in value:
            return name
    return value


def assess_action_safety(
    action: Any, *, camera: str | None, capture_phase: str | None
) -> dict[str, Any]:
    """Fail closed; this policy never authorizes robot motion by itself."""
    reasons: list[str] = []
    valid, errors = validate_action_completion(action)
    if not valid:
        reasons.extend(errors)
    else:
        if action["orientation"] == "unknown":
            reasons.append("orientation is unknown")
        if action["grasp_region"] == "unknown":
            reasons.append("grasp region is unknown")

    family = camera_family(camera)
    if family != "video6":
        reasons.append(
            f"camera {family!r} is blocked; held-out support is currently limited to video6"
        )
    if capture_phase == "after_grasp":
        reasons.append("after_grasp is observation-only and cannot plan a new grasp")
    elif capture_phase != "before_grasp":
        reasons.append(f"unsupported capture phase: {capture_phase!r}")

    blocking = bool(reasons)
    return {
        "status": "blocked" if blocking else "review_required",
        "camera_family": family,
        "capture_phase": capture_phase,
        "reasons": reasons
        if reasons
        else ["human approval is required before any robot action"],
        "requires_human_approval": True,
        "robot_motion_allowed": False,
    }
