"""Opt-in body-tracking gate for sequential absolute-position rollouts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

BODY_JOINTS = (
    "shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
    "wrist_flex.pos", "wrist_roll.pos",
)


class TrackingGateError(RuntimeError):
    """Tracking cannot continue with the current observations/target."""


@dataclass(frozen=True)
class TrackingDecision:
    hold: bool
    errors_deg: dict[str, float]
    target_age_s: float


class BodyTrackingGate:
    """Pause sequence advancement, not motor updates, until a body target is met.

    Gripper feedback is deliberately excluded: contact with an object can
    prevent it from reaching its empty-jaw target. Tolerances are sequencing
    parameters, not a collision-safe workspace or a proof of task success.
    """

    def __init__(self, tolerances_deg: dict[str, float], max_target_wait_s: float, measurement_margin_deg: float = 0.):
        if set(tolerances_deg) != set(BODY_JOINTS):
            raise ValueError("Tracking gate must specify exactly the five body joints")
        self.tolerances = {k: float(v) for k, v in tolerances_deg.items()}
        if any(not math.isfinite(v) or not 0 < v <= 5 for v in self.tolerances.values()):
            raise ValueError("Tracking tolerances must be finite, >0 and <=5 degrees")
        self.max_wait = float(max_target_wait_s)
        if not math.isfinite(self.max_wait) or not 0 < self.max_wait <= 5:
            raise ValueError("Tracking target timeout must be finite, >0 and <=5 seconds")
        self.margin = float(measurement_margin_deg)
        if not math.isfinite(self.margin) or not 0 <= self.margin <= .1:
            raise ValueError("Measurement margin must be finite and between 0 and 0.1 degrees")
        self.target: dict[str, float] | None = None
        self.issued_at = 0.0

    @classmethod
    def from_json(cls, text: str):
        config = json.loads(text)
        if config.get("schema_version") != 1:
            raise ValueError("Unsupported tracking gate schema_version")
        return cls(config["body_tolerance_deg"], config["max_target_wait_s"], config.get("measurement_margin_deg", 0.))

    @staticmethod
    def _finite_values(values: dict, label: str) -> dict[str, float]:
        result = {}
        for joint in BODY_JOINTS:
            if joint not in values:
                raise TrackingGateError(f"Missing {label} for {joint}")
            value = float(values[joint])
            if not math.isfinite(value):
                raise TrackingGateError(f"Non-finite {label} for {joint}")
            result[joint] = value
        return result

    def set_target(self, target: dict, now: float) -> None:
        self.target = self._finite_values(target, "body target")
        self.issued_at = now

    def inspect(self, measured: dict, now: float) -> TrackingDecision:
        positions = self._finite_values(measured, "body observation")
        if self.target is None:
            return TrackingDecision(False, {}, 0.0)
        errors = {k: abs(self.target[k] - positions[k]) for k in BODY_JOINTS}
        hold = any(errors[k] > self.tolerances[k] + self.margin for k in BODY_JOINTS)
        age = max(0.0, now - self.issued_at)
        if hold and age >= self.max_wait:
            raise TrackingGateError(
                f"Body tracking timed out after {age:.3f}s; errors_deg={errors}"
            )
        return TrackingDecision(hold, errors, age)
