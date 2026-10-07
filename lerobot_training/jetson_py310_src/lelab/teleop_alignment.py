"""Pure startup blending for calibrated absolute-position teleoperation."""

import math

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


class AbsoluteTeleopAlignment:
    """Blend startup only; never retain a leader/follower position offset.

    The caller must retain its startup motor-command clamp until ready is True.
    This is a pose transition, not collision checking or workspace certification.
    """

    blend_s = 3.0
    timeout_s = 15.0
    body_tolerance_deg = 4.1
    gripper_tolerance = 5.0

    def __init__(self, follower_start, started_at):
        self.start = self._positions(follower_start, "follower")
        self.started_at = float(started_at)
        self.ready = False

    @staticmethod
    def _positions(values, label):
        result = {}
        for motor in JOINTS:
            value = float(values[motor])
            if not math.isfinite(value):
                raise ValueError(f"Non-finite {label} position: {motor}")
            result[motor] = value
        return result

    def command(self, leader_action, measured, now):
        leader = self._positions({motor: leader_action[f"{motor}.pos"] for motor in JOINTS}, "leader")
        leader["gripper"] = max(0.0, min(100.0, leader["gripper"]))
        if self.ready:
            return {f"{motor}.pos": value for motor, value in leader.items()}, True
        positions = self._positions(measured, "follower")
        elapsed = max(0.0, float(now) - self.started_at)
        if elapsed >= self.blend_s:
            self.ready = all(
                abs(positions[motor] - leader[motor]) <= (
                    self.gripper_tolerance if motor == "gripper" else self.body_tolerance_deg
                ) for motor in JOINTS
            )
        if not self.ready and elapsed >= self.timeout_s:
            raise RuntimeError("Teleoperation startup alignment timed out; direct following was not enabled")
        progress = min(1.0, elapsed / self.blend_s)
        blend = progress * progress * (3.0 - 2.0 * progress)
        action = {
            f"{motor}.pos": self.start[motor] + blend * (leader[motor] - self.start[motor])
            for motor in JOINTS
        }
        action["gripper.pos"] = max(0.0, min(100.0, action["gripper.pos"]))
        return action, self.ready
