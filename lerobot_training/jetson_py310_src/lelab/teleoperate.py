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

import logging
import math
import threading
import time
from typing import Any

from pydantic import BaseModel

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

from .utils.config import setup_calibration_files
from .utils.devices import safe_disconnect_device
from .teleop_alignment import AbsoluteTeleopAlignment

logger = logging.getLogger(__name__)

# Global variables for teleoperation state
teleoperation_active = False
teleoperation_thread: threading.Thread | None = None
current_robot = None
current_teleop = None
last_start_gate: dict[str, Any] | None = None
teleoperation_phase = "idle"
# Guards the start path; the worker owns disconnect so stop() does not race.
_state_lock = threading.Lock()

TELEOP_BODY_START_MAX_DELTA_DEG = 15.0
TELEOP_WRIST_FLEX_START_MAX_DELTA_DEG = 35.0
TELEOP_WRIST_ROLL_START_MAX_DELTA_DEG = 20.0
TELEOP_GRIPPER_START_MAX_DELTA = 20.0
# Direct body-joint teleoperation requested by the operator. A 5-degree
# per-command cap still clipped normal leader motion at 20 Hz and caused a
# visible lag. 360 degrees is effectively unrestricted within the calibrated
# joint range. The gripper's 0–100 normalized range is also direct following
# at the operator's request. Starting pose differences are reported but do
# not block teleoperation; startup-only blending prevents a first-command snap.
TELEOP_MAX_RELATIVE_TARGET = {
    "shoulder_pan": 360.0,
    "shoulder_lift": 360.0,
    "elbow_flex": 360.0,
    "wrist_flex": 360.0,
    "wrist_roll": 360.0,
    "gripper": 100.0,
}
TELEOP_CONTROL_HZ = 20.0
TELEOP_MAX_SESSION_S = 180.0
TELEOP_MODE = "absolute_leader_pose_with_startup_alignment"
TELEOP_STARTUP_MAX_RELATIVE_TARGET = {
    name: (2.0 if name == "gripper" else 5.0) for name in TELEOP_MAX_RELATIVE_TARGET
}


class TeleoperateRequest(BaseModel):
    leader_port: str
    follower_port: str
    leader_config: str
    follower_config: str


def get_joint_positions_from_robot(robot) -> dict[str, float]:
    """
    Extract current joint positions from the robot and convert to URDF joint format.

    lerobot drives the SO-101 with ``use_degrees=True`` by default, so each
    ``observation["<motor>.pos"]`` is already the joint angle in degrees relative
    to the calibration center — which is also the URDF's zero pose. The URDF
    joint value is therefore just that angle converted to radians, for every
    joint. (The gripper reports 0–100 rather than degrees, but that range lands
    inside the Jaw limit, matching the open/closed sweep.)

    Args:
        robot: The robot instance (SO101Follower)

    Returns:
        Dictionary mapping URDF joint names to radian values
    """
    motor_to_urdf_mapping = {
        "shoulder_pan": "Rotation",
        "shoulder_lift": "Pitch",
        "elbow_flex": "Elbow",
        "wrist_flex": "Wrist_Pitch",
        "wrist_roll": "Wrist_Roll",
        "gripper": "Jaw",
    }

    try:
        # The 3D preview only needs motor positions. get_observation() also
        # waits for every camera frame, which can stall the teleop worker when
        # one of the three USB cameras is slow or unavailable.
        positions = robot.bus.sync_read("Present_Position", num_retry=2)

        joint_positions: dict[str, float] = {}
        debug_rows = []
        for motor_name, urdf_joint_name in motor_to_urdf_mapping.items():
            if motor_name not in positions:
                logger.warning(f"Motor {motor_name} not found in position read")
                joint_positions[urdf_joint_name] = 0.0
                continue

            angle_degrees = positions[motor_name]
            joint_positions[urdf_joint_name] = angle_degrees * math.pi / 180.0
            debug_rows.append(f"{motor_name:14s} {angle_degrees:+8.2f}° → {urdf_joint_name:11s}")

        # Throttled debug print (~once per second at 20 Hz broadcast).
        now = time.time()
        if now - getattr(get_joint_positions_from_robot, "_last_log", 0) > 1.0:
            get_joint_positions_from_robot._last_log = now
            logger.info("[joint-debug]\n  " + "\n  ".join(debug_rows))

        return joint_positions

    except Exception as e:
        logger.error(f"Error getting joint positions: {e}")
        return dict.fromkeys(motor_to_urdf_mapping.values(), 0.0)


def _safe_disconnect(device) -> None:
    """Disconnect a robot/teleop device, swallowing (but logging) any error.

    Used on the connection-failure cleanup path so one device's failure can't
    leave the other holding its serial port open.
    """
    safe_disconnect_device(device, logger)


def _disconnect_preserve_torque(device) -> None:
    """Release a serial port without changing the device's torque state."""
    if device is None:
        return
    try:
        bus = getattr(device, "bus", None)
        if bus is not None and bus.is_connected:
            bus.disconnect(disable_torque=False)
    except Exception as exc:
        logger.warning(f"Could not disconnect while preserving torque: {exc}")


def evaluate_start_gate(
    follower_positions: dict[str, float], leader_positions: dict[str, float]
) -> dict[str, Any]:
    """Compare passive position reads before any calibration/configuration write."""
    expected = {
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    }
    missing = sorted(
        (expected - set(follower_positions)) | (expected - set(leader_positions))
    )
    if missing:
        return {"passed": False, "missing": missing, "deltas": {}, "violations": missing}
    deltas = {
        name: abs(float(follower_positions[name]) - float(leader_positions[name]))
        for name in sorted(expected)
    }
    limits = {}
    for name in expected:
        if name == "gripper":
            limits[name] = TELEOP_GRIPPER_START_MAX_DELTA
        elif name == "wrist_flex":
            limits[name] = TELEOP_WRIST_FLEX_START_MAX_DELTA_DEG
        elif name == "wrist_roll":
            limits[name] = TELEOP_WRIST_ROLL_START_MAX_DELTA_DEG
        else:
            limits[name] = TELEOP_BODY_START_MAX_DELTA_DEG
    violations = {
        name: {"delta": round(deltas[name], 3), "limit": limits[name]}
        for name in sorted(expected)
        if deltas[name] > limits[name]
    }
    return {
        "passed": not violations,
        "missing": [],
        "deltas": {name: round(value, 3) for name, value in deltas.items()},
        "violations": violations,
    }


def handle_start_teleoperation(request: TeleoperateRequest, websocket_manager=None) -> dict[str, Any]:
    """Handle start teleoperation request.

    Connects to both arms *synchronously* so that a connection failure (arm
    unplugged, port busy, power off) is reported back to the caller, rather than
    dying silently in the worker thread while the API has already claimed
    success. Only the teleoperation loop runs in the background thread.
    """
    global teleoperation_active, teleoperation_thread, current_robot, current_teleop, last_start_gate, teleoperation_phase

    from . import record as _record, rollout as _rollout

    with _state_lock:
        if teleoperation_active:
            return {"success": False, "message": "Teleoperation is already active"}
        if _record.recording_active:
            return {"success": False, "message": "Recording is currently active. Stop it first."}
        if _rollout.inference_active:
            return {"success": False, "message": "Inference is currently active. Stop it first."}
        teleoperation_active = True

    robot = None
    teleop_device = None
    try:
        logger.info(
            f"Starting teleoperation with leader port: {request.leader_port}, follower port: {request.follower_port}"
        )

        # Setup calibration files
        leader_config_name, follower_config_name = setup_calibration_files(
            request.leader_config, request.follower_config
        )

        # Create robot and teleop configs
        robot_config = SO101FollowerConfig(
            port=request.follower_port,
            id=follower_config_name,
            max_relative_target=TELEOP_STARTUP_MAX_RELATIVE_TARGET,
        )

        teleop_config = SO101LeaderConfig(
            port=request.leader_port,
            id=leader_config_name,
        )

        # Connect synchronously. If either device fails to connect, clean up the
        # other (so its serial port is released) and report the error — do NOT
        # leave the caller thinking teleoperation started.
        logger.info("Initializing robot and teleop device...")
        robot = SO101Follower(robot_config)
        teleop_device = SO101Leader(teleop_config)

        # Connect each arm separately so the error names which one failed and
        # tells the user what to do, instead of a generic "failed to start".
        logger.info("Connecting to follower arm...")
        try:
            robot.bus.connect()
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to the follower arm on {request.follower_port}. "
                "Make sure it's plugged in and powered on, then try again."
            ) from e

        logger.info("Connecting to leader arm...")
        try:
            teleop_device.bus.connect()
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to the leader arm on {request.leader_port}. "
                "Make sure it's plugged in and powered on, then try again."
            ) from e

        # Read both arms before configuration; differences remain diagnostic.
        # Startup blending removes the initial offset rather than retaining it.
        follower_start = robot.bus.sync_read("Present_Position", num_retry=2)
        leader_start = teleop_device.bus.sync_read("Present_Position", num_retry=2)
        last_start_gate = evaluate_start_gate(follower_start, leader_start)
        last_start_gate["enforced"] = False
        logger.info(f"Teleoperation start pose (informational): {last_start_gate}")
        if last_start_gate["missing"]:
            raise RuntimeError(
                "Could not read all arm joints before teleoperation: "
                f"{last_start_gate['missing']}"
            )

        # Write calibration to motors' memory after the passive position read.
        logger.info("Writing calibration to motors...")
        robot.bus.write_calibration(robot.calibration)
        teleop_device.bus.write_calibration(teleop_device.calibration)

        # Connect cameras and configure motors
        logger.info("Connecting cameras and configuring motors...")
        for cam in robot.cameras.values():
            cam.connect()
        robot.configure()
        teleop_device.configure()
        # Configuration can take time. Re-sample both arms immediately before
        # starting the worker so motion during setup cannot create a first-
        # command jump from stale offsets.
        follower_start = robot.bus.sync_read("Present_Position", num_retry=2)
        leader_start = teleop_device.bus.sync_read("Present_Position", num_retry=2)
        missing_after_setup = sorted(
            set(TELEOP_MAX_RELATIVE_TARGET) - set(follower_start)
            | set(TELEOP_MAX_RELATIVE_TARGET) - set(leader_start)
        )
        if missing_after_setup:
            raise RuntimeError(
                f"Could not read all arm joints after configuration: {missing_after_setup}"
            )
        logger.info("Successfully connected to both devices")

        current_robot = robot
        current_teleop = teleop_device
        teleoperation_phase = "aligning"

        # Stream the arms in the background; the worker owns disconnect so stop()
        # does not race the serial bus from the request thread.
        def teleoperation_worker():
            global teleoperation_active, current_robot, current_teleop, teleoperation_phase

            logger.info("Starting teleoperation loop...")
            try:
                last_broadcast_time = 0
                broadcast_interval = 0.05  # 20 FPS
                session_started = time.monotonic()
                control_period = 1.0 / TELEOP_CONTROL_HZ
                alignment = AbsoluteTeleopAlignment(follower_start, session_started)

                while teleoperation_active:
                    loop_started = time.monotonic()
                    if loop_started - session_started >= TELEOP_MAX_SESSION_S:
                        logger.warning("Teleoperation stopped at the safety session timeout")
                        teleoperation_active = False
                        break
                    leader_action = teleop_device.get_action()
                    measured = robot.bus.sync_read("Present_Position", num_retry=2) if not alignment.ready else None
                    absolute_action, aligned = alignment.command(leader_action, measured, time.monotonic())
                    if not teleoperation_active:
                        break
                    if aligned and teleoperation_phase != "following":
                        robot.config.max_relative_target = TELEOP_MAX_RELATIVE_TARGET
                        teleoperation_phase = "following"
                        logger.info("Startup alignment complete; following absolute leader positions without offset")
                    robot.send_action(absolute_action)

                    current_time = time.time()
                    if current_time - last_broadcast_time >= broadcast_interval:
                        try:
                            joint_positions = get_joint_positions_from_robot(robot)
                            joint_data = {
                                "type": "joint_update",
                                "joints": joint_positions,
                                "timestamp": current_time,
                            }
                            if websocket_manager and websocket_manager.active_connections:
                                websocket_manager.broadcast_joint_data_sync(joint_data)
                            last_broadcast_time = current_time
                        except Exception as e:
                            logger.error(f"Error broadcasting joint data: {e}")

                    remaining = control_period - (time.monotonic() - loop_started)
                    if remaining > 0:
                        time.sleep(remaining)
            except Exception as e:
                logger.error(f"Error during teleoperation loop: {e}")
            finally:
                if teleoperation_phase != "following":
                    # An aborted alignment must not disable torque and drop the
                    # gravity-loaded arm. No guessed recovery position is sent.
                    try:
                        positions = robot.bus.sync_read("Present_Position", num_retry=2)
                        AbsoluteTeleopAlignment._positions(positions, "stop hold")
                        robot.send_action({f"{name}.pos": positions[name] for name in TELEOP_MAX_RELATIVE_TARGET})
                    except Exception:
                        logger.exception("Could not establish alignment-stop hold; operator recovery required")
                    robot.config.disable_torque_on_disconnect = False
                    logger.warning("Alignment stopped; retaining existing motor torque for operator recovery")
                _safe_disconnect(robot)
                _safe_disconnect(teleop_device)
                logger.info("Teleoperation stopped")
                teleoperation_active = False
                current_robot = None
                current_teleop = None
                teleoperation_phase = "idle"

        teleoperation_thread = threading.Thread(
            target=teleoperation_worker, name="teleoperation-worker", daemon=True
        )
        teleoperation_thread.start()

        return {
            "success": True,
            "message": "Teleoperation started successfully",
            "leader_port": request.leader_port,
            "follower_port": request.follower_port,
            "mode": TELEOP_MODE,
        }

    except Exception as e:
        # Connection (or setup) failed before the loop started: release any
        # device that did open, reset state, and surface the error.
        _safe_disconnect(robot)
        _safe_disconnect(teleop_device)
        teleoperation_active = False
        current_robot = None
        current_teleop = None
        teleoperation_phase = "idle"
        logger.error(f"Failed to start teleoperation: {e}")
        # str(e) is already a user-facing message for the connection failures
        # raised above; the toast title supplies the "error starting" context.
        return {"success": False, "message": str(e)}


def handle_stop_teleoperation() -> dict[str, Any]:
    """Handle stop teleoperation request.

    Signals the worker via `teleoperation_active = False` and waits for it to
    exit. The worker owns the disconnect call, so this avoids racing the
    serial bus from the request thread.
    """
    global teleoperation_active, teleoperation_thread

    if not teleoperation_active:
        return {"success": False, "message": "No teleoperation session is active"}

    logger.info("Stop teleoperation triggered from web interface")
    teleoperation_active = False

    worker = teleoperation_thread
    if worker is not None and worker.is_alive():
        worker.join(timeout=5.0)
        if worker.is_alive():
            logger.warning("Teleoperation worker did not exit within 5s")
    teleoperation_thread = None

    return {"success": True, "message": "Teleoperation stopped successfully"}


def handle_teleoperation_status() -> dict[str, Any]:
    """Handle teleoperation status request"""
    return {
        "teleoperation_active": teleoperation_active,
        "phase": teleoperation_phase,
        "available_controls": {
            "stop_teleoperation": teleoperation_active,
        },
        "safety": {
            "start_pose_check_enforced": False,
            "body_start_max_delta_deg": TELEOP_BODY_START_MAX_DELTA_DEG,
            "wrist_flex_start_max_delta_deg": TELEOP_WRIST_FLEX_START_MAX_DELTA_DEG,
            "wrist_roll_start_max_delta_deg": TELEOP_WRIST_ROLL_START_MAX_DELTA_DEG,
            "gripper_start_max_delta": TELEOP_GRIPPER_START_MAX_DELTA,
            "max_relative_target": TELEOP_MAX_RELATIVE_TARGET,
            "control_hz": TELEOP_CONTROL_HZ,
            "max_session_s": TELEOP_MAX_SESSION_S,
            "mode": TELEOP_MODE,
            "startup_blend_s": AbsoluteTeleopAlignment.blend_s,
            "startup_timeout_s": AbsoluteTeleopAlignment.timeout_s,
            "startup_max_relative_target": TELEOP_STARTUP_MAX_RELATIVE_TARGET,
            "last_start_gate": last_start_gate,
        },
        "message": "Teleoperation status retrieved successfully",
    }


def handle_get_joint_positions() -> dict[str, Any]:
    """Handle get current robot joint positions request"""
    global current_robot

    if not teleoperation_active or current_robot is None:
        return {"success": False, "message": "No active teleoperation session"}

    try:
        joint_positions = get_joint_positions_from_robot(current_robot)
        return {"success": True, "joint_positions": joint_positions, "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Error getting joint positions: {e}")
        return {"success": False, "message": f"Failed to get joint positions: {str(e)}"}
