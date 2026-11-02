from __future__ import annotations

import base64
import fcntl
import json
import math
import os
import re
import select
import signal
import sys
import tempfile
import termios
import threading
import time
import tty
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import Request, urlopen

from .errors import SorterError


JOINTS = (
    "shoulder_pan", "shoulder_lift", "elbow_flex",
    "wrist_flex", "wrist_roll", "gripper",
)
ARM_JOINTS = JOINTS[:-1]
POSE_NAMES = (
    "home",
    "gripper_open", "gripper_closed",
    "slot1_pre", "slot1_grasp", "slot1_lift",
    "slot2_pre", "slot2_grasp", "slot2_lift",
    "slot3_pre", "slot3_grasp", "slot3_lift",
    "red_above", "red_release",
    "blue_above", "blue_release",
    "green_above", "green_release",
)
TYPE_GRIPPER_POSES = (
    "gripper_closed_A", "gripper_closed_B", "gripper_closed_C",
)
FINISH_POSE_NAME = "finish"
ALL_POSE_NAMES = POSE_NAMES + TYPE_GRIPPER_POSES + (FINISH_POSE_NAME,)
MAX_TEMPERATURE_C = 55
PRESTART_MAX_TEMPERATURE_C_EXCLUSIVE = 50
WARM_START_MAX_TEMPERATURE_C_EXCLUSIVE = 52
ORIGINAL_CONTROL_PERIOD_S = 0.1
SMOOTH_CONTROL_HZ_CHOICES = (30, 40, 50)
DEFAULT_SMOOTH_CONTROL_HZ = 40
FREE_SPACE_VELOCITY_CHOICES = (20, 30, 40)
DEFAULT_FREE_SPACE_VELOCITY = 20
MOTION_PROFILES = ("original", "smooth")
DEFAULT_DEMO_MOTION_PROFILE = "smooth"
TRANSIT_SETTLE_STAGE_NAMES = frozenset({
    "home",
    "slot1_lift", "slot2_lift", "slot3_lift",
    "red_above", "blue_above", "green_above",
})
TRANSIT_ELBOW_SETTLE_TOLERANCE_DEG = 8.0
TRANSIT_SETTLE_DWELL_S = 0.5
SMOOTH_FEEDBACK_PERIOD_S = 0.1
SMOOTH_SAFETY_PERIOD_S = 1.0
MOTION_LOG_SCHEMA_VERSION = 4
DEADLINE_MISS_TOLERANCE_S = 0.005
GRIPPER_CONTACT_RELIEF_BIAS = 1.0
TEACH_GOAL_PRESENT_RAW_TOLERANCE = 40
DEFAULT_ALREADY_ON_GOAL_PRESENT_RAW_TOLERANCE = 10
MAX_SUPERVISED_HOLD_GOAL_PRESENT_RAW_TOLERANCE = 60
POST_GOAL_READ_DELAY_S = 0.2
POST_GOAL_READ_RETRIES = 3
VALID_BOTTLE_TYPES = {"A", "B", "C"}
VALID_DESTINATIONS = {"red", "blue", "green"}
GRIPPER_POSES = {"gripper_open", "gripper_closed", *TYPE_GRIPPER_POSES}

SERVO_STATUS_ERRORS = (
    ("Overload error", "OVERLOAD", 32),
    ("Overheat error", "OVERHEAT", 4),
    ("OverEle error", "OVER_CURRENT", 8),
    ("Input voltage error", "VOLTAGE", 1),
    ("Angle sen error", "ANGLE_SENSOR", 2),
)


def _fail(code: str, observed: Any, expected: str, remedy: str) -> SorterError:
    return SorterError(code, observed, expected, remedy)


class SegmentSafetyFault(RuntimeError):
    """A motion-time read or servo fault whose original device detail must survive."""

    def __init__(self, code: str, details: Mapping[str, Any]):
        super().__init__(code)
        self.code = code
        self.details = dict(details)


@dataclass(frozen=True)
class MarkerRoute:
    marker_id: int
    bottle_type: str
    slot: str
    destination: str
    center_px: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "bottle_type": self.bottle_type,
            "slot": self.slot,
            "destination": self.destination,
            "marker_center_px": list(self.center_px),
        }


@dataclass(frozen=True)
class DemoSettings:
    pose_path: Path
    snapshot_url: str
    snapshot_max_age_s: float
    image_size_px: tuple[int, int]
    coordinate_space: str
    marker_to_type: dict[int, str]
    destinations: dict[str, str]
    slots: dict[str, tuple[float, float, float, float]]
    expected_bottle_slots: dict[str, str]
    roi_status: str
    control_period_s: float
    max_step_deg: float
    max_gripper_step: float
    velocity: dict[str, float]
    acceleration: dict[str, float]
    max_segment_travel: dict[str, float]
    settle_tolerance: dict[str, float]
    settle_timeout_s: float


def load_demo_settings(config) -> DemoSettings:
    raw = config.raw.get("fixed_demo")
    if not isinstance(raw, dict):
        raise _fail("fixed_demo_config_missing", raw, "fixed_demo object",
                    "configs/system.example.json의 fixed_demo 설정을 확인해 주세요")
    try:
        pose_path = Path(raw["pose_file"])
        if not pose_path.is_absolute():
            pose_path = (config.path.parent.parent / pose_path).resolve()
        size = tuple(int(v) for v in raw["image_size_px"])
        slots = {
            str(name): tuple(float(v) for v in rect)
            for name, rect in raw["slot_pixel_rois"].items()
        }
        marker_to_type = {int(k): str(v) for k, v in raw["marker_id_to_type"].items()}
        expected_bottle_slots = {
            str(label): str(slot) for label, slot in raw["expected_bottle_slots"].items()
        }
        destinations = {str(k): str(v) for k, v in raw["destination_rules"].items()}
        motion = raw["motion"]
        velocity = {joint: float(motion["max_velocity"][joint]) for joint in JOINTS}
        acceleration = {joint: float(motion["max_acceleration"][joint]) for joint in JOINTS}
        travel = {joint: float(motion["max_segment_travel"][joint]) for joint in JOINTS}
        tolerance = {joint: float(motion["settle_tolerance"][joint]) for joint in JOINTS}
        settings = DemoSettings(
            pose_path=pose_path,
            snapshot_url=str(raw["camera_snapshot_url"]),
            snapshot_max_age_s=float(raw["snapshot_max_age_s"]),
            image_size_px=(size[0], size[1]),
            coordinate_space=str(raw["pixel_coordinate_space"]),
            marker_to_type=marker_to_type,
            destinations=destinations,
            slots=slots,
            expected_bottle_slots=expected_bottle_slots,
            roi_status=str(raw.get("slot_roi_status", "UNSPECIFIED")),
            control_period_s=float(motion["control_period_s"]),
            max_step_deg=float(motion["max_emitted_step_deg"]),
            max_gripper_step=float(motion["max_gripper_step_percent"]),
            velocity=velocity,
            acceleration=acceleration,
            max_segment_travel=travel,
            settle_tolerance=tolerance,
            settle_timeout_s=float(motion["settle_timeout_s"]),
        )
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise _fail("fixed_demo_config_invalid", f"{type(error).__name__}: {error}",
                    "complete fixed_demo schema", "설정의 누락·수치 형식을 확인해 주세요") from error
    validate_demo_settings(settings)
    return settings


def validate_demo_settings(settings: DemoSettings) -> None:
    if settings.image_size_px != (640, 480):
        raise ValueError("fixed_demo_image_size_must_match_astra_640x480")
    if settings.coordinate_space != "undistorted_full_frame":
        raise ValueError("fixed_demo_coordinate_space_invalid")
    if set(settings.slots) != {"slot1", "slot2", "slot3"}:
        raise ValueError("fixed_demo_requires_slot1_slot2_slot3")
    width, height = settings.image_size_px
    for name, rect in settings.slots.items():
        if len(rect) != 4:
            raise ValueError(f"invalid_slot_rectangle:{name}")
        x1, y1, x2, y2 = rect
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError(f"slot_rectangle_out_of_frame:{name}")
    names = sorted(settings.slots)
    for index, first_name in enumerate(names):
        a = settings.slots[first_name]
        for second_name in names[index + 1:]:
            b = settings.slots[second_name]
            if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                raise ValueError(f"overlapping_slot_rois:{first_name}:{second_name}")
    if settings.marker_to_type != {7: "C", 8: "B", 9: "A"}:
        raise ValueError("fixed_demo_marker_mapping_must_be_7C_8B_9A")
    if settings.expected_bottle_slots != {"A": "slot1", "B": "slot2", "C": "slot3"}:
        raise ValueError("fixed_demo_expected_slots_must_match_verified_layout")
    if settings.roi_status not in {"verified", "provisional_verify_in_dry_run"}:
        raise ValueError("fixed_demo_slot_roi_status_invalid")
    if (not set(settings.destinations).issubset(VALID_BOTTLE_TYPES)
            or not set(settings.destinations.values()).issubset(VALID_DESTINATIONS)):
        raise ValueError("fixed_demo_default_destination_rules_invalid")
    if not 0 < settings.control_period_s <= 0.2:
        raise ValueError("invalid_control_period")
    if not 0 < settings.max_step_deg <= 2.0:
        raise ValueError("two_degree_limit_must_not_be_relaxed")
    if not 0 < settings.max_gripper_step <= 2.0:
        raise ValueError("invalid_gripper_step_limit")
    for values in (settings.velocity, settings.acceleration,
                   settings.max_segment_travel, settings.settle_tolerance):
        if set(values) != set(JOINTS) or any(not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError("invalid_joint_motion_limits")


def fetch_snapshot(url: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read(3_000_000))
    except Exception as error:
        raise _fail("camera_snapshot_failed", f"{type(error).__name__}: {error}",
                    "Astra manual-frame.json 1회 응답", "8020 카메라 화면 상태를 확인해 주세요") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshot"), dict):
        raise _fail("camera_snapshot_invalid", type(payload).__name__, "snapshot object",
                    "카메라 서비스 버전을 확인해 주세요")
    return payload["snapshot"]


def fetch_snapshot_with_jpeg(url: str, *, timeout_s: float = 3.0) -> tuple[dict[str, Any], bytes]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read(6_000_000))
    except Exception as error:
        raise _fail("camera_snapshot_failed", f"{type(error).__name__}: {error}",
                    "Astra manual-frame.json 1회 응답", "8020 카메라 화면 상태를 확인해 주세요") from error
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    jpeg_base64 = payload.get("jpeg_base64") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict) or not isinstance(jpeg_base64, str):
        raise _fail("camera_frame_bundle_invalid", type(payload).__name__,
                    "snapshot and jpeg_base64", "8020 카메라 서비스 버전을 확인해 주세요")
    try:
        jpeg = base64.b64decode(jpeg_base64, validate=True)
    except (ValueError, TypeError) as error:
        raise _fail("camera_jpeg_base64_invalid", str(error), "valid base64 JPEG",
                    "8020 카메라 프레임 응답을 확인해 주세요") from error
    if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
        raise _fail("camera_jpeg_invalid", len(jpeg), "complete JPEG",
                    "8020 카메라 프레임 응답을 확인해 주세요")
    return snapshot, jpeg


def slot_for_pixel(point: Sequence[float], slots: Mapping[str, Sequence[float]]) -> str:
    if len(point) != 2 or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                              or not math.isfinite(float(v)) for v in point):
        raise ValueError("invalid_marker_center")
    x, y = map(float, point)
    matched = [name for name, (x1, y1, x2, y2) in slots.items()
               if x1 <= x < x2 and y1 <= y < y2]
    if len(matched) != 1:
        raise ValueError(f"marker_center_not_in_exactly_one_slot:{x:.3f},{y:.3f}:{matched}")
    return matched[0]


def roi_preview_annotations(snapshot: Mapping[str, Any], settings: DemoSettings) -> dict[str, Any]:
    if snapshot.get("ok") is not True:
        raise _fail("camera_not_ready", snapshot.get("error"), "ok=true",
                    "Astra 카메라 서비스를 확인해 주세요")
    age = snapshot.get("frame_age_s")
    if not isinstance(age, (int, float)) or isinstance(age, bool) or age > settings.snapshot_max_age_s:
        raise _fail("camera_frame_stale", age, f"<={settings.snapshot_max_age_s}s",
                    "카메라 프레임이 갱신되는지 확인해 주세요")
    table = snapshot.get("table")
    if not isinstance(table, Mapping):
        raise _fail("aruco_result_missing", table, "table marker result",
                    "Astra 서비스에 ArUco 작업대 설정이 로드됐는지 확인해 주세요")
    if table.get("coordinate_space") != settings.coordinate_space:
        raise _fail("pixel_coordinate_space_mismatch", table.get("coordinate_space"),
                    settings.coordinate_space, "dry-run과 같은 왜곡 보정 프레임을 사용해 주세요")
    if tuple(table.get("source_frame_size_px", ())) != settings.image_size_px:
        raise _fail("camera_image_size_mismatch", table.get("source_frame_size_px"),
                    str(list(settings.image_size_px)), "카메라 해상도와 ROI 설정을 맞춰 주세요")
    centers = table.get("detected_marker_centers_px")
    if not isinstance(centers, Mapping):
        raise _fail("aruco_centers_missing", centers, "detected_marker_centers_px",
                    "ArUco 검출 상태를 확인해 주세요")
    markers = []
    for marker_text, point in centers.items():
        try:
            marker_id = int(marker_text)
        except (TypeError, ValueError, IndexError) as error:
            raise _fail("aruco_center_invalid", {marker_text: point}, "ID -> [x, y]",
                        "카메라 marker center 출력을 확인해 주세요") from error
        if (not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(float(value)) for value in point)):
            raise _fail("aruco_center_invalid", {marker_text: point}, "ID -> [finite x, y]",
                        "카메라 marker center 출력을 확인해 주세요")
        x, y = float(point[0]), float(point[1])
        matched = [name for name, (x1, y1, x2, y2) in settings.slots.items()
                   if x1 <= x < x2 and y1 <= y < y2]
        slot = matched[0] if len(matched) == 1 else "OUTSIDE"
        markers.append({"marker_id": marker_id,
                        "bottle_type": settings.marker_to_type.get(marker_id),
                        "center_px": [x, y], "slot": slot})
    markers.sort(key=lambda item: item["marker_id"])
    return {
        "frame_sequence": snapshot.get("sequence"),
        "frame_age_s": age,
        "pixel_coordinate_space": settings.coordinate_space,
        "image_size_px": list(settings.image_size_px),
        "slots": {name: list(rect) for name, rect in settings.slots.items()},
        "markers": markers,
    }


def _render_roi_preview_png(jpeg: bytes, annotations: Mapping[str, Any], output: Path) -> None:
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise _fail("roi_preview_opencv_missing", str(error), "LeLab Python with OpenCV",
                    "기존 LeLab Python 절대 경로로 실행해 주세요") from error
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    expected_width, expected_height = annotations["image_size_px"]
    if image is None or image.shape[:2] != (expected_height, expected_width):
        shape = None if image is None else list(image.shape)
        raise _fail("camera_jpeg_size_mismatch", shape,
                    f"{expected_width}x{expected_height}", "8020 프레임 크기를 확인해 주세요")
    colors = {"slot1": (255, 120, 0), "slot2": (0, 200, 255), "slot3": (120, 255, 80)}
    for name, rect in annotations["slots"].items():
        x1, y1, x2, y2 = (round(value) for value in rect)
        color = colors[name]
        cv2.rectangle(image, (x1, y1), (x2 - 1, y2 - 1), color, 2)
        cv2.putText(image, name, (x1 + 4, max(18, y1 + 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    for marker in annotations["markers"]:
        x, y = (round(value) for value in marker["center_px"])
        slot = marker["slot"]
        color = colors.get(slot, (0, 0, 255))
        cv2.drawMarker(image, (x, y), color, cv2.MARKER_CROSS, 18, 2)
        bottle = f"/{marker['bottle_type']}" if marker["bottle_type"] else ""
        label = f"ID{marker['marker_id']}{bottle} {slot} ({x},{y})"
        cv2.putText(image, label, (min(x + 8, expected_width - 220), max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2, cv2.LINE_AA)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise _fail("roi_preview_write_failed", str(output), "writable PNG path",
                    "출력 폴더 권한을 확인해 주세요")


def save_roi_preview(settings: DemoSettings, output: Path, *,
                     bundle_loader: Callable[[str], tuple[dict[str, Any], bytes]] = fetch_snapshot_with_jpeg,
                     renderer: Callable[[bytes, Mapping[str, Any], Path], None] = _render_roi_preview_png,
                     ) -> dict[str, Any]:
    if output.suffix.lower() != ".png":
        raise _fail("roi_preview_output_must_be_png", str(output), "*.png",
                    "--output 경로를 .png로 끝내 주세요")
    snapshot, jpeg = bundle_loader(settings.snapshot_url)
    annotations = roi_preview_annotations(snapshot, settings)
    renderer(jpeg, annotations, output)
    return {
        "status": "ROI_PREVIEW_SAVED",
        "output": str(output.resolve()),
        **annotations,
        "serial_open": 0,
        "motor_command": 0,
    }


def parse_destination_rules(tokens: Sequence[str] | None,
                            defaults: Mapping[str, str]) -> dict[str, str]:
    if tokens is None:
        rules = {str(label).upper(): str(color).lower()
                 for label, color in defaults.items()}
    else:
        rules = {}
        for token in tokens:
            if not isinstance(token, str) or token.count("=") != 1:
                raise _fail("destination_rule_format_invalid", token, "A=red",
                            "--rule C=green B=red A=blue 형식으로 입력해 주세요")
            label, color = (part.strip() for part in token.split("=", 1))
            label, color = label.upper(), color.lower()
            if label not in VALID_BOTTLE_TYPES:
                raise _fail("destination_rule_bottle_invalid", label, "A, B, or C",
                            "약통 유형을 A/B/C 중 하나로 지정해 주세요")
            if color not in VALID_DESTINATIONS:
                raise _fail("destination_rule_color_invalid", color, "red, blue, or green",
                            "바구니를 red/blue/green 중 하나로 지정해 주세요")
            if label in rules:
                raise _fail("destination_rule_duplicate_bottle", label, "one rule per bottle type",
                            "같은 약통 유형은 한 번만 지정해 주세요")
            rules[label] = color
    if (not set(rules).issubset(VALID_BOTTLE_TYPES)
            or not set(rules.values()).issubset(VALID_DESTINATIONS)):
        raise _fail("default_destination_rules_invalid", rules,
                    "A/B/C -> red/blue/green", "fixed_demo.destination_rules를 확인해 주세요")
    return rules


def routes_from_snapshot(snapshot: Mapping[str, Any], settings: DemoSettings,
                         rules: Mapping[str, str] | None = None) -> tuple[list[MarkerRoute], list[dict]]:
    active_rules = (parse_destination_rules(None, settings.destinations) if rules is None else
                    parse_destination_rules([f"{label}={color}" for label, color in rules.items()], {}))
    if snapshot.get("ok") is not True:
        raise _fail("camera_not_ready", snapshot.get("error"), "ok=true",
                    "Astra 카메라 서비스를 확인해 주세요")
    age = snapshot.get("frame_age_s")
    if not isinstance(age, (int, float)) or isinstance(age, bool) or age > settings.snapshot_max_age_s:
        raise _fail("camera_frame_stale", age, f"<={settings.snapshot_max_age_s}s",
                    "카메라 프레임이 갱신되는지 확인해 주세요")
    table = snapshot.get("table")
    if not isinstance(table, Mapping):
        raise _fail("aruco_result_missing", table, "table marker result",
                    "Astra 서비스에 ArUco 작업대 설정이 로드됐는지 확인해 주세요")
    if table.get("coordinate_space") != settings.coordinate_space:
        raise _fail("pixel_coordinate_space_mismatch", table.get("coordinate_space"),
                    settings.coordinate_space, "resize/crop 없는 왜곡 보정 프레임을 사용해 주세요")
    if tuple(table.get("source_frame_size_px", ())) != settings.image_size_px:
        raise _fail("camera_image_size_mismatch", table.get("source_frame_size_px"),
                    str(list(settings.image_size_px)), "카메라 해상도와 ROI 설정을 맞춰 주세요")
    centers = table.get("detected_marker_centers_px")
    if not isinstance(centers, Mapping):
        raise _fail("aruco_centers_missing", centers, "detected_marker_centers_px",
                    "ArUco 검출 상태를 확인해 주세요")
    routes: list[MarkerRoute] = []
    ignored: list[dict] = []
    for marker_id, bottle_type in settings.marker_to_type.items():
        center = centers.get(str(marker_id))
        if center is None:
            continue
        try:
            slot = slot_for_pixel(center, settings.slots)
        except ValueError as error:
            raise _fail("bottle_marker_outside_slot", {"id": marker_id, "center": center},
                        "configured slot1..3 중 정확히 하나",
                        "약통을 고정 칸 안에 놓고 dry-run을 다시 확인해 주세요") from error
        destination = active_rules.get(bottle_type)
        if destination is None:
            ignored.append({"marker_id": marker_id, "bottle_type": bottle_type,
                            "slot": slot, "reason": "not_selected_by_rule"})
            continue
        routes.append(MarkerRoute(marker_id, bottle_type, slot, destination,
                                  (float(center[0]), float(center[1]))))
    routes.sort(key=lambda route: route.center_px[0])
    if not routes:
        raise _fail("no_routable_bottle_marker", {"detected": sorted(centers), "ignored": ignored},
                    "slot 안의 rule에 선택된 ID7(C)/ID8(B)/ID9(A)",
                    "--rule에 선택한 약통 마커가 보이게 놓아 주세요")
    return routes, ignored


def verify_expected_marker_layout(snapshot: Mapping[str, Any],
                                  settings: DemoSettings) -> dict[str, Any]:
    annotations = roi_preview_annotations(snapshot, settings)
    observed = {
        item["bottle_type"]: item
        for item in annotations["markers"]
        if item["bottle_type"] in VALID_BOTTLE_TYPES
    }
    failures = []
    for bottle_type, expected_slot in settings.expected_bottle_slots.items():
        marker = observed.get(bottle_type)
        if marker is None:
            failures.append({"bottle_type": bottle_type, "reason": "marker_missing",
                             "expected_slot": expected_slot})
        elif marker["slot"] != expected_slot:
            failures.append({"bottle_type": bottle_type, "marker_id": marker["marker_id"],
                             "reason": "unexpected_slot", "expected_slot": expected_slot,
                             "observed_slot": marker["slot"],
                             "center_px": marker["center_px"]})
    layout = {
        "status": "PASS" if not failures else "FAIL",
        "expected": dict(settings.expected_bottle_slots),
        "observed": {
            label: {"marker_id": item["marker_id"], "slot": item["slot"],
                    "center_px": item["center_px"]}
            for label, item in observed.items()
        },
        "failures": failures,
    }
    if failures:
        raise _fail("expected_marker_layout_mismatch", layout,
                    "A=slot1, B=slot2, C=slot3 with all three markers visible",
                    "세 약통을 검증된 칸에 놓고 카메라 인식을 확인해 주세요")
    return layout


def stage_names_for_routes(routes: Sequence[MarkerRoute]) -> list[str]:
    stages = []
    for route in routes:
        stages.extend((
            "home", "gripper_open", f"{route.slot}_pre", f"{route.slot}_grasp",
            f"gripper_closed_{route.bottle_type}",
            f"{route.slot}_lift", f"{route.destination}_above",
            f"{route.destination}_release", "gripper_open",
            f"{route.destination}_above", "home",
        ))
    if stages:
        stages.append(FINISH_POSE_NAME)
    return stages


def save_pose_file(path: Path, *, follower_serial: str, poses: Mapping[str, Mapping[str, Any]]) -> None:
    payload = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "follower_serial": follower_serial,
        "units": {"arm": "degree", "gripper": "percent", "raw": "servo_raw"},
        "poses": poses,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def partial_pose_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.partial{path.suffix}")


def load_partial_pose_file(path: Path, *, expected_serial: str) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _fail("partial_pose_file_load_failed", str(error), str(path),
                    "기존 partial pose JSON을 확인해 주세요") from error
    if raw.get("schema_version") != 1 or raw.get("follower_serial") != expected_serial:
        raise _fail("partial_pose_identity_mismatch", raw.get("follower_serial"), expected_serial,
                    "현재 follower에서 새로 티칭해 주세요")
    poses = raw.get("poses")
    if not isinstance(poses, dict):
        raise _fail("partial_pose_values_invalid", type(poses).__name__, "poses object",
                    "partial pose JSON을 확인해 주세요")
    expected_prefix = list(POSE_NAMES[:len(poses)])
    if list(poses) != expected_prefix:
        raise _fail("partial_pose_order_invalid", list(poses), str(expected_prefix),
                    "순서대로 저장된 partial pose만 --resume할 수 있습니다")
    for name, item in poses.items():
        position = item.get("position") if isinstance(item, dict) else None
        present_raw = item.get("present_raw") if isinstance(item, dict) else None
        if (not isinstance(position, dict) or set(position) != set(JOINTS)
                or not isinstance(present_raw, dict) or set(present_raw) != set(JOINTS)):
            raise _fail("partial_pose_values_invalid", name, "six joint positions and raw values",
                        "partial pose JSON을 확인해 주세요")
    return dict(poses)


def load_pose_records(path: Path, *, expected_serial: str,
                      required_names: Sequence[str]) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _fail("pose_file_load_failed", str(error), str(path),
                    "teach 명령으로 모든 자세를 먼저 저장해 주세요") from error
    if raw.get("schema_version") != 1 or raw.get("follower_serial") != expected_serial:
        raise _fail("pose_file_identity_mismatch", raw.get("follower_serial"), expected_serial,
                    "현재 follower에서 자세를 다시 티칭해 주세요")
    poses = raw.get("poses")
    missing = ([name for name in required_names if name not in poses]
               if isinstance(poses, dict) else list(required_names))
    if missing:
        raise _fail("pose_file_incomplete", missing, "all fixed demo poses",
                    "기본 teach를 완료하고 종류별 닫힘은 teach --only로 저장해 주세요")
    result = {}
    for name, record in poses.items():
        if name not in ALL_POSE_NAMES:
            raise _fail("pose_name_invalid", name, "known fixed demo pose",
                        "pose JSON의 알 수 없는 자세를 제거해 주세요")
        values = record.get("position") if isinstance(record, dict) else None
        present_raw = record.get("present_raw") if isinstance(record, dict) else None
        if (not isinstance(values, dict) or set(values) != set(JOINTS)
                or not isinstance(present_raw, dict) or set(present_raw) != set(JOINTS)):
            raise _fail("pose_values_invalid", name, "six joint positions and raw values",
                        "해당 자세를 다시 티칭해 주세요")
        converted = {joint: float(values[joint]) for joint in JOINTS}
        if any(not math.isfinite(value) for value in converted.values()):
            raise _fail("pose_values_nonfinite", name, "finite joint positions",
                        "pose JSON을 확인해 주세요")
        result[name] = dict(record)
        result[name]["position"] = converted
    return result


def load_pose_file(path: Path, *, expected_serial: str) -> dict[str, dict[str, float]]:
    records = load_pose_records(path, expected_serial=expected_serial,
                                required_names=ALL_POSE_NAMES)
    result = {}
    for name in ALL_POSE_NAMES:
        result[name] = dict(records[name]["position"])
    return result


def smooth_interpolation(start: Mapping[str, float], target: Mapping[str, float],
                         settings: DemoSettings, *, profile: str = "cubic"
                         ) -> list[dict[str, float]]:
    if set(start) != set(JOINTS) or set(target) != set(JOINTS):
        raise ValueError("interpolation_joint_keys_invalid")
    delta = {joint: float(target[joint]) - float(start[joint]) for joint in JOINTS}
    violations = {joint: abs(value) for joint, value in delta.items()
                  if abs(value) > settings.max_segment_travel[joint]}
    if violations:
        raise ValueError(f"segment_travel_limit_exceeded:{violations}")
    duration = trajectory_duration(delta, settings, profile=profile)
    count = max(1, math.ceil(duration / settings.control_period_s))
    while True:
        points = [trajectory_position(start, delta, index / count, profile=profile)
                  for index in range(1, count + 1)]
        previous = {joint: float(start[joint]) for joint in JOINTS}
        okay = True
        for point in points:
            for joint in JOINTS:
                limit = settings.max_gripper_step if joint == "gripper" else settings.max_step_deg
                if abs(point[joint] - previous[joint]) > limit + 1e-9:
                    okay = False
            previous = point
        if okay:
            return points
        count += 1


def trajectory_duration(delta: Mapping[str, float], settings: DemoSettings, *,
                        profile: str = "cubic") -> float:
    """Minimum smoothstep duration under configured velocity/acceleration limits."""
    if profile == "cubic":
        velocity_factor = 1.5
        acceleration_factor = 6.0
    elif profile == "quintic":
        velocity_factor = 1.875
        acceleration_factor = 10.0 / math.sqrt(3.0)
    else:
        raise ValueError(f"trajectory_profile_invalid:{profile}")
    duration = settings.control_period_s
    for joint, distance in delta.items():
        duration = max(duration,
                       velocity_factor * abs(distance) / settings.velocity[joint],
                       math.sqrt(acceleration_factor * abs(distance)
                                 / settings.acceleration[joint]))
    return duration


def trajectory_position(start: Mapping[str, float], delta: Mapping[str, float], u: float,
                        *, profile: str = "cubic"
                        ) -> dict[str, float]:
    u = min(1.0, max(0.0, float(u)))
    if profile == "cubic":
        fraction = u * u * (3.0 - 2.0 * u)
    elif profile == "quintic":
        fraction = u ** 3 * (10.0 - 15.0 * u + 6.0 * u * u)
    else:
        raise ValueError(f"trajectory_profile_invalid:{profile}")
    return {joint: float(start[joint]) + fraction * float(delta[joint]) for joint in JOINTS}


def _hard_cap_synchronized(previous: Mapping[str, float], requested: Mapping[str, float],
                           settings: DemoSettings) -> dict[str, float]:
    """Keep joint ratios synchronized while treating 2 degrees as a hard cap only."""
    delta = {joint: float(requested[joint]) - float(previous[joint]) for joint in JOINTS}
    fraction = 1.0
    for joint, value in delta.items():
        limit = settings.max_gripper_step if joint == "gripper" else settings.max_step_deg
        if abs(value) > limit:
            fraction = min(fraction, limit / abs(value))
    return {joint: float(previous[joint]) + fraction * delta[joint] for joint in JOINTS}


def _direction_reversals(values: Sequence[float], *, epsilon: float = 1e-9) -> int:
    signs = [1 if value > epsilon else -1 for value in values if abs(value) > epsilon]
    return sum(current != previous for previous, current in zip(signs, signs[1:]))


def _mean_std(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, math.sqrt(variance)


def planned_trajectory_metrics(start: Mapping[str, float], points: Sequence[Mapping[str, float]],
                               period_s: float, settings: DemoSettings) -> dict[str, Any]:
    previous = {joint: float(start[joint]) for joint in JOINTS}
    deltas = {joint: [] for joint in JOINTS}
    for point in points:
        for joint in JOINTS:
            deltas[joint].append(float(point[joint]) - previous[joint])
        previous = {joint: float(point[joint]) for joint in JOINTS}
    per_joint = {}
    for joint in JOINTS:
        absolute = [abs(value) for value in deltas[joint]]
        velocity = [value / period_s for value in deltas[joint]]
        velocity_with_stops = [0.0, *velocity, 0.0]
        acceleration = [
            (current - previous) / period_s
            for previous, current in zip(velocity_with_stops, velocity_with_stops[1:])
        ]
        acceleration_with_stops = [0.0, *acceleration, 0.0]
        jerk = [
            (current - previous) / period_s
            for previous, current in zip(acceleration_with_stops,
                                         acceleration_with_stops[1:])
        ]
        limit = settings.max_gripper_step if joint == "gripper" else settings.max_step_deg
        per_joint[joint] = {
            "net_delta": sum(deltas[joint]),
            "emitted_delta_mean": sum(absolute) / len(absolute) if absolute else 0.0,
            "max_delta": max(absolute, default=0.0),
            "first_delta": deltas[joint][0] if deltas[joint] else 0.0,
            "last_delta": deltas[joint][-1] if deltas[joint] else 0.0,
            "max_velocity": max((abs(value) for value in velocity), default=0.0),
            "max_acceleration": max((abs(value) for value in acceleration), default=0.0),
            "max_discrete_jerk": max((abs(value) for value in jerk), default=0.0),
            "direction_reversals": _direction_reversals(deltas[joint]),
            "hard_cap": limit,
            "hard_cap_binding_cycles": sum(abs(value - limit) <= 1e-6 for value in absolute),
            "near_95pct_hard_cap_cycles": sum(value >= 0.95 * limit for value in absolute),
        }
    return {
        "command_count": len(points),
        "command_interval_mean_s": period_s if points else None,
        "command_interval_std_s": 0.0 if points else None,
        "duration_s": len(points) * period_s,
        "joints": per_joint,
    }


def analyze_motion_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("motion_log_empty")
    timestamps = [int(row["command_monotonic_ns"]) for row in rows]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("motion_log_timestamps_not_strictly_increasing")
    intervals = [(current - previous) / 1_000_000_000
                 for previous, current in zip(timestamps, timestamps[1:])]
    interval_mean, interval_std = _mean_std(intervals)
    planned = [row.get("planned_monotonic_ns") for row in rows]
    lateness = [
        (timestamps[index] - int(planned[index])) / 1_000_000_000
        for index in range(len(rows)) if planned[index] is not None
    ]
    write_durations = [float(row["write_duration_s"]) for row in rows
                       if row.get("write_duration_s") is not None]
    write_duration_mean, write_duration_std = _mean_std(write_durations)
    feedback_rows = [row for row in rows
                     if isinstance(row.get("present_position"), Mapping)
                     and isinstance(row.get("goal_present_error"), Mapping)
                     and isinstance(row.get("moving"), Mapping)]
    feedback_timestamps = [
        int(row.get("feedback_monotonic_ns") or row["command_monotonic_ns"])
        for row in feedback_rows
    ]
    if any(current <= previous
           for previous, current in zip(feedback_timestamps, feedback_timestamps[1:])):
        raise ValueError("motion_feedback_timestamps_not_strictly_increasing")
    feedback_intervals = [
        (current - previous) / 1_000_000_000
        for previous, current in zip(feedback_timestamps, feedback_timestamps[1:])
    ]
    feedback_interval_mean, feedback_interval_std = _mean_std(feedback_intervals)
    raw_feedback_available = bool(feedback_rows) and all(
        isinstance(row.get("present_raw"), Mapping) for row in feedback_rows)
    goal_raw_feedback_available = bool(feedback_rows) and all(
        isinstance(row.get("goal_raw"), Mapping) for row in feedback_rows)
    torque_rows = [row for row in rows if isinstance(row.get("torque_enable"), Mapping)]
    temperature_rows = [row for row in rows if isinstance(row.get("temperatures_c"), Mapping)]
    per_joint = {}
    for joint in JOINTS:
        command = [float(row["command_position"][joint]) for row in rows]
        present = [float(row["present_position"][joint]) for row in feedback_rows]
        error = [float(row["goal_present_error"][joint]) for row in feedback_rows]
        moving = [int(row["moving"][joint]) for row in feedback_rows]
        command_delta = [current - previous for previous, current in zip(command, command[1:])]
        command_delta_abs = [abs(value) for value in command_delta]
        present_velocity = [(present[index] - present[index - 1]) / feedback_intervals[index - 1]
                            for index in range(1, len(present))]
        error_delta = [current - previous for previous, current in zip(error, error[1:])]
        velocity_mean, velocity_std = _mean_std(present_velocity)
        command_delta_mean, command_delta_std = _mean_std(command_delta_abs)
        error_mean, error_std = _mean_std(error)
        per_joint[joint] = {
            "emitted_delta_per_cycle": command_delta,
            "emitted_delta_abs_mean": command_delta_mean,
            "emitted_delta_abs_std": command_delta_std,
            "max_delta": max((abs(value) for value in command_delta), default=0.0),
            "direction_reversals": _direction_reversals(command_delta),
            "present_velocity_approx_mean": velocity_mean,
            "present_velocity_approx_std": velocity_std,
            "present_velocity_approx_max_abs": max(
                (abs(value) for value in present_velocity), default=0.0),
            "present_velocity_direction_reversals": _direction_reversals(
                present_velocity),
            "goal_present_error_mean": error_mean,
            "goal_present_error_std": error_std,
            "goal_present_error_peak_to_peak": max(error, default=0.0) - min(error, default=0.0),
            "goal_present_error_max_abs": max((abs(value) for value in error), default=0.0),
            "goal_present_error_direction_reversals": _direction_reversals(error_delta),
            "moving_true_cycles": sum(value != 0 for value in moving),
            "feedback_sample_count": len(feedback_rows),
        }
        if raw_feedback_available:
            present_raw = [int(row["present_raw"][joint]) for row in feedback_rows]
            per_joint[joint].update({
                "present_raw_start": present_raw[0],
                "present_raw_final": present_raw[-1],
                "present_raw_net_delta": present_raw[-1] - present_raw[0],
                "present_raw_max_cycle_delta": max(
                    (abs(current - previous)
                     for previous, current in zip(present_raw, present_raw[1:])),
                    default=0),
            })
            if goal_raw_feedback_available:
                goal_raw = [int(row["goal_raw"][joint]) for row in feedback_rows]
                per_joint[joint].update({
                    "goal_raw_start": goal_raw[0],
                    "goal_raw_final": goal_raw[-1],
                    "goal_raw_net_delta": goal_raw[-1] - goal_raw[0],
                })
            else:
                per_joint[joint].update({
                    "goal_raw_start": None,
                    "goal_raw_final": None,
                    "goal_raw_net_delta": None,
                })
    return {
        "sample_count": len(rows),
        "command_interval_mean_s": interval_mean,
        "command_interval_std_s": interval_std,
        "command_interval_max_s": max(intervals, default=None),
        "deadline_miss_tolerance_s": DEADLINE_MISS_TOLERANCE_S,
        "deadline_miss_count": sum(
            value > DEADLINE_MISS_TOLERANCE_S for value in lateness),
        "deadline_lateness_max_s": max(lateness, default=None),
        "write_duration_mean_s": write_duration_mean,
        "write_duration_std_s": write_duration_std,
        "write_duration_max_s": max(write_durations, default=None),
        "feedback_sample_count": len(feedback_rows),
        "feedback_interval_mean_s": feedback_interval_mean,
        "feedback_interval_std_s": feedback_interval_std,
        "feedback_interval_max_s": max(feedback_intervals, default=None),
        "raw_feedback_available": raw_feedback_available,
        "goal_raw_feedback_available": goal_raw_feedback_available,
        "goal_sources": sorted({str(row.get("goal_source", "legacy_unspecified"))
                                for row in feedback_rows}),
        "torque_enable_cycles": (
            [{joint: int(row["torque_enable"][joint]) for joint in JOINTS}
             for row in torque_rows] if torque_rows else "NOT_RECORDED"),
        "moving_cycles": [
            {joint: int(row["moving"][joint]) for joint in JOINTS}
            for row in feedback_rows],
        "temperature_samples": [
            {"sequence": int(row["sequence"]),
             "temperatures_c": {joint: int(row["temperatures_c"][joint])
                                for joint in JOINTS}}
            for row in temperature_rows],
        "joints": per_joint,
    }


def load_and_analyze_motion_log(path: Path) -> dict[str, Any]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise _fail("motion_log_load_failed", str(error), str(path),
                    "motion-test가 저장한 JSONL을 지정해 주세요") from error
    try:
        return analyze_motion_rows(rows)
    except (KeyError, TypeError, ValueError) as error:
        raise _fail("motion_log_invalid", str(error), "complete motion telemetry rows",
                    "원본 로그를 수정하지 말고 다시 확인해 주세요") from error


def compare_actual_motion_logs(original_log: Path, smooth_log: Path) -> dict[str, Any]:
    original = load_and_analyze_motion_log(original_log)
    smooth = load_and_analyze_motion_log(smooth_log)

    def maximum(analysis: Mapping[str, Any], key: str) -> float:
        return max(float(analysis["joints"][joint][key]) for joint in ARM_JOINTS)

    start_raw_difference = {}
    final_raw_difference = {}
    raw_comparable = (original.get("raw_feedback_available") is True
                      and smooth.get("raw_feedback_available") is True)
    if raw_comparable:
        for joint in ARM_JOINTS:
            original_joint = original["joints"][joint]
            smooth_joint = smooth["joints"][joint]
            start_raw_difference[joint] = (
                smooth_joint["present_raw_start"] - original_joint["present_raw_start"])
            final_raw_difference[joint] = (
                smooth_joint["present_raw_final"] - original_joint["present_raw_final"])

    joints = {}
    for joint in ARM_JOINTS:
        a = original["joints"][joint]
        b = smooth["joints"][joint]
        joints[joint] = {
            "original_max_step_deg": a["max_delta"],
            "smooth_max_step_deg": b["max_delta"],
            "original_max_tracking_error_deg": a["goal_present_error_max_abs"],
            "smooth_max_tracking_error_deg": b["goal_present_error_max_abs"],
            "original_present_velocity_reversals": a["present_velocity_direction_reversals"],
            "smooth_present_velocity_reversals": b["present_velocity_direction_reversals"],
            "start_present_raw_difference_smooth_minus_original": (
                start_raw_difference.get(joint)),
            "final_present_raw_difference_smooth_minus_original": (
                final_raw_difference.get(joint)),
        }

    return {
        "status": "LIVE_MOTION_A_B_ANALYSIS",
        "original_log": str(original_log),
        "smooth_log": str(smooth_log),
        "original": original,
        "smooth": smooth,
        "comparison": {
            "raw_start_end_comparable": raw_comparable,
            "start_present_raw_difference_smooth_minus_original": start_raw_difference,
            "final_present_raw_difference_smooth_minus_original": final_raw_difference,
            "original_actual_interval_mean_s": original["command_interval_mean_s"],
            "original_interval_std_s": original["command_interval_std_s"],
            "original_max_interval_s": original["command_interval_max_s"],
            "original_max_step_deg": maximum(original, "max_delta"),
            "original_max_tracking_error_deg": maximum(
                original, "goal_present_error_max_abs"),
            "smooth_actual_interval_mean_s": smooth["command_interval_mean_s"],
            "smooth_interval_std_s": smooth["command_interval_std_s"],
            "smooth_max_interval_s": smooth["command_interval_max_s"],
            "smooth_max_step_deg": maximum(smooth, "max_delta"),
            "smooth_max_tracking_error_deg": maximum(
                smooth, "goal_present_error_max_abs"),
            "user_visual_original": "REQUIRED",
            "user_visual_smooth": "REQUIRED",
            "smoothing_effective": "INCONCLUSIVE_NEEDS_USER_VISUAL_AND_START_MATCH_REVIEW",
        },
        "joints": joints,
        "motor_command": 0,
        "serial_open": 0,
    }


def compare_pose_motion(config, settings: DemoSettings, *, from_pose: str, to_pose: str,
                        actual_log: Path | None = None) -> dict[str, Any]:
    if (from_pose not in ALL_POSE_NAMES or to_pose not in ALL_POSE_NAMES
            or from_pose in GRIPPER_POSES or to_pose in GRIPPER_POSES
            or from_pose == to_pose):
        raise _fail("motion_compare_pose_invalid", [from_pose, to_pose],
                    "two different arm pose names",
                    "home/slot/basket/finish 중 서로 다른 두 자세를 지정해 주세요")
    poses = load_pose_file(settings.pose_path,
                           expected_serial=config.raw["robot"]["follower_serial"])
    start = dict(poses[from_pose])
    target = _arm_only(start, poses[to_pose])
    original_settings = replace(settings, control_period_s=ORIGINAL_CONTROL_PERIOD_S)
    live_smooth_settings = replace(
        settings, control_period_s=1.0 / DEFAULT_SMOOTH_CONTROL_HZ)
    original_points = smooth_interpolation(start, target, original_settings)
    smooth_points = smooth_interpolation(start, target, settings)
    quintic_points = smooth_interpolation(
        start, target, live_smooth_settings, profile="quintic")
    original_metrics = planned_trajectory_metrics(
        start, original_points, original_settings.control_period_s, original_settings)
    smooth_metrics = planned_trajectory_metrics(
        start, smooth_points, settings.control_period_s, settings)
    quintic_metrics = planned_trajectory_metrics(
        start, quintic_points, live_smooth_settings.control_period_s,
        live_smooth_settings)
    original_max_step = max(
        original_metrics["joints"][joint]["max_delta"] for joint in ARM_JOINTS)
    smooth_max_step = max(
        smooth_metrics["joints"][joint]["max_delta"] for joint in ARM_JOINTS)
    quintic_max_step = max(
        quintic_metrics["joints"][joint]["max_delta"] for joint in ARM_JOINTS)
    actual_analysis = (load_and_analyze_motion_log(actual_log)
                       if actual_log is not None else "NOT_AVAILABLE")
    result = {
        "status": "MOTION_A_B_OFFLINE",
        "from_pose": from_pose,
        "to_pose": to_pose,
        "same_start_end_pose": True,
        "original": original_metrics,
        "smooth": smooth_metrics,
        "quintic": quintic_metrics,
        "smoothing_method": "shared quintic smoothstep with absolute-deadline live scheduler",
        "two_degree_cap_removed": False,
        "torque_gain_changed": False,
        "ik_changed": False,
        "calibration_changed": False,
        "actual_log_analysis": actual_analysis,
        "JITTER_ROOT_CAUSE": (
            "UNCONFIRMED_CURRENT_FIXED_DEMO_CYCLE_LOG_NOT_AVAILABLE"
            if actual_log is None else
            "MEASURED_CADENCE_AND_TRACKING_REPORTED_CAUSALITY_NOT_ASSUMED"),
        "ORIGINAL_COMMAND_INTERVAL": {
            "mean_s": original_settings.control_period_s, "std_s": 0.0,
            "source": "planned_not_measured"},
        "ORIGINAL_MAX_STEP": original_max_step,
        "ORIGINAL_DIRECTION_REVERSALS": {
            joint: original_metrics["joints"][joint]["direction_reversals"]
            for joint in ARM_JOINTS},
        "SMOOTHING_METHOD": (
            "live candidate: s(t)=10t^3-15t^4+6t^5 shared by all arm joints; "
            "absolute deadlines"),
        "CONTROL_PERIOD": settings.control_period_s,
        "SMOOTH_MAX_STEP": smooth_max_step,
        "QUINTIC_METHOD": "s(t)=10t^3-15t^4+6t^5; live motion-test candidate",
        "QUINTIC_CONTROL_PERIOD": live_smooth_settings.control_period_s,
        "QUINTIC_CONTROL_HZ": DEFAULT_SMOOTH_CONTROL_HZ,
        "QUINTIC_MAX_STEP": quintic_max_step,
        "LIVE_SMOOTH_MAX_STEP": quintic_max_step,
        "EXPECTED_IMPROVEMENT": (
            "relative-sleep drift is not accumulated; expired ticks are skipped; "
            "2deg remains a synchronized hard cap"),
        "TORQUE_GAIN_CHANGED": "NO",
        "IK_CHANGED": "NO",
        "READY_FOR_SMOOTH_MOTION_TEST": "NO_NEEDS_FREE_SPACE_START_END_CONFIRMATION",
        "serial_open": 0,
        "motor_command": 0,
    }
    return result


class MotionTraceRecorder:
    """Append diagnostic telemetry outside Git; never authorizes a motor write."""

    def __init__(self, path: Path):
        if path.suffix.lower() != ".jsonl":
            raise _fail("motion_log_path_invalid", str(path), "*.jsonl",
                        ".local 아래의 새 JSONL 경로를 지정해 주세요")
        allowed_root = (Path.cwd() / ".local" / "fixed-demo-motion").resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(allowed_root):
            raise _fail("motion_log_outside_ignored_root", str(resolved),
                        str(allowed_root / "<new>.jsonl"),
                        "원본 telemetry가 Git에 들어가지 않도록 .local/fixed-demo-motion을 사용해 주세요")
        path = resolved
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file = path.open("x", encoding="utf-8")
        except FileExistsError as error:
            raise _fail("motion_log_already_exists", str(path), "new path",
                        "기존 원본 로그를 보존하고 새 파일명을 사용해 주세요") from error
        self.path = path
        self.count = 0
        self.previous_write_monotonic_ns: int | None = None

    def record(self, *, stage: str, profile: str, sequence: int,
               scheduled_offset_s: float, planned_monotonic_ns: int,
               command_monotonic_ns: int, write_completed_monotonic_ns: int,
               command_position: Mapping[str, float],
               present_position: Mapping[str, float] | None,
               goal_position: Mapping[str, float] | None,
               present_raw: Mapping[str, int] | None,
               goal_raw: Mapping[str, int] | None,
               torque_enable: Mapping[str, int] | None,
               moving: Mapping[str, int] | None,
               feedback_monotonic_ns: int | None = None,
               temperatures_c: Mapping[str, int] | None = None,
               goal_source: str = "register_readback") -> None:
        present_payload = (None if present_position is None else
                           {joint: float(present_position[joint]) for joint in JOINTS})
        goal_payload = (None if goal_position is None else
                        {joint: float(goal_position[joint]) for joint in JOINTS})
        row = {
            "schema_version": MOTION_LOG_SCHEMA_VERSION,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "profile": profile,
            "sequence": sequence,
            "scheduled_offset_s": scheduled_offset_s,
            "planned_monotonic_ns": planned_monotonic_ns,
            "actual_write_monotonic_ns": command_monotonic_ns,
            "write_completed_monotonic_ns": write_completed_monotonic_ns,
            "write_duration_s": (
                write_completed_monotonic_ns - command_monotonic_ns) / 1_000_000_000,
            "actual_interval_s": (
                None if self.previous_write_monotonic_ns is None else
                (command_monotonic_ns - self.previous_write_monotonic_ns) / 1_000_000_000
            ),
            "command_monotonic_ns": command_monotonic_ns,
            "command_position": {joint: float(command_position[joint]) for joint in JOINTS},
            "feedback_sampled": present_payload is not None,
            "feedback_monotonic_ns": feedback_monotonic_ns,
            "present_position": present_payload,
            "goal_position": goal_payload,
            "goal_source": goal_source,
            "present_raw": (None if present_raw is None else
                            {joint: int(present_raw[joint]) for joint in JOINTS}),
            "goal_raw": (None if goal_raw is None else
                         {joint: int(goal_raw[joint]) for joint in JOINTS}),
            "torque_enable": (None if torque_enable is None else
                              {joint: int(torque_enable[joint]) for joint in JOINTS}),
            "goal_present_error": (
                None if present_payload is None or goal_payload is None else
                {joint: goal_payload[joint] - present_payload[joint] for joint in JOINTS}
            ),
            "moving": (None if moving is None else
                       {joint: int(moving[joint]) for joint in JOINTS}),
            "temperatures_c": (None if temperatures_c is None else
                               {joint: int(temperatures_c[joint]) for joint in JOINTS}),
        }
        self.file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.file.flush()
        self.previous_write_monotonic_ns = command_monotonic_ns
        self.count += 1

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()


def _is_canonical_acm_port(port: Path) -> bool:
    return port.parent == Path("/dev") and re.fullmatch(r"ttyACM\d+", port.name) is not None


def _serial_for_port(port: Path) -> str:
    if not _is_canonical_acm_port(port):
        raise RuntimeError(f"noncanonical_port:{port}")
    tty_device = (Path("/sys/class/tty") / port.name / "device").resolve()
    for directory in (tty_device, *tty_device.parents):
        candidate = directory / "serial"
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
    raise RuntimeError(f"usb_serial_not_found:{port}")


def resolve_robot_ports(follower_serial: str, leader_serial: str, *,
                        candidates: Sequence[Path] | None = None,
                        serial_reader: Callable[[Path], str] = _serial_for_port,
                        dev_root: Path = Path("/dev")) -> tuple[Path, Path]:
    """Resolve both SO-101 roles by USB serial before either serial port is opened."""
    if (not follower_serial or not leader_serial
            or follower_serial == leader_serial):
        raise RuntimeError("leader_follower_serial_identity_collision")
    ports = tuple(sorted(
        (Path(port) for port in (candidates if candidates is not None
                                 else dev_root.glob("ttyACM*"))
         if _is_canonical_acm_port(Path(port))),
        key=lambda port: port.name,
    ))
    matches: dict[str, list[Path]] = {follower_serial: [], leader_serial: []}
    for port in ports:
        try:
            serial = serial_reader(port)
        except (OSError, RuntimeError):
            continue
        if serial in matches:
            matches[serial].append(port)
    for role, serial in (("follower", follower_serial), ("leader", leader_serial)):
        found = matches[serial]
        if not found:
            raise RuntimeError(f"{role}_usb_serial_not_found:{serial}")
        if len(found) != 1:
            raise RuntimeError(f"{role}_usb_serial_ambiguous:{serial}:{found}")
    follower_port = matches[follower_serial][0]
    leader_port = matches[leader_serial][0]
    if follower_port == leader_port:
        raise RuntimeError("leader_follower_port_identity_collision")
    return follower_port, leader_port


class PortLock:
    """Unprivileged process lock; TIOCEXCL seals the actual opened serial fd."""

    def __init__(self, role: str):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
        self.path = runtime / f"medicine-sorter-{os.getuid()}-{role}.lock"
        self.file = None

    def acquire(self) -> None:
        self.file = self.path.open("a+")
        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        if self.file is not None:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def _raw(bus, register: str, *, num_retry: int = 0) -> dict[str, int]:
    values = bus.sync_read(register, normalize=False, num_retry=num_retry)
    if set(values) != set(JOINTS) or any(type(v) is not int for v in values.values()):
        raise RuntimeError(f"invalid_raw_register:{register}")
    return {joint: values[joint] for joint in JOINTS}


def _positions(bus) -> dict[str, float]:
    return _normalized_positions(bus, "Present_Position")


def _normalized_positions(bus, register: str) -> dict[str, float]:
    values = bus.sync_read(register, normalize=True, num_retry=0)
    if set(values) != set(JOINTS):
        raise RuntimeError(f"invalid_normalized_register:{register}")
    result = {joint: float(values[joint]) for joint in JOINTS}
    if any(not math.isfinite(value) for value in result.values()):
        raise RuntimeError(f"nonfinite_normalized_register:{register}")
    return result


def _normalize_raw_positions(bus, values: Mapping[str, int]) -> dict[str, float]:
    if set(values) != set(JOINTS) or not callable(getattr(bus, "_normalize", None)):
        raise RuntimeError("raw_position_normalization_unavailable")
    by_id = {bus.motors[joint].id: int(values[joint]) for joint in JOINTS}
    normalized = bus._normalize(by_id)
    result = {joint: float(normalized[bus.motors[joint].id]) for joint in JOINTS}
    if any(not math.isfinite(value) for value in result.values()):
        raise RuntimeError("nonfinite_normalized_raw_position")
    return result


def _motor_id(bus, joint: str) -> int:
    motor = getattr(bus, "motors", {}).get(joint)
    motor_id = getattr(motor, "id", None)
    return int(motor_id) if motor_id is not None else JOINTS.index(joint) + 1


def _servo_status_from_exception(error: BaseException) -> tuple[str | None, int | None]:
    message = str(error)
    for fragment, status, bit in SERVO_STATUS_ERRORS:
        if fragment in message:
            return status, bit
    return None, None


def _read_motor_register(bus, register: str, joint: str, *, normalize: bool,
                         num_retry: int, stage: str) -> int | float:
    try:
        return bus.read(register, joint, normalize=normalize, num_retry=num_retry)
    except Exception as error:
        status, bit = _servo_status_from_exception(error)
        raise SegmentSafetyFault(
            "servo_status_error" if status is not None else "motor_register_read_failed",
            {
                "stage": stage,
                "register": register,
                "motor_name": joint,
                "motor_id": _motor_id(bus, joint),
                "register_value": None,
                "servo_status": status or "UNKNOWN",
                "servo_status_bit": bit,
                "servo_status_value": bit,
                "driver_error": str(error),
            },
        ) from error


def _read_temperatures(bus, *, stage: str) -> dict[str, int]:
    temperatures: dict[str, int] = {}
    for joint in JOINTS:
        try:
            temperatures[joint] = int(_read_motor_register(
                bus, "Present_Temperature", joint, normalize=False,
                num_retry=0, stage=stage))
        except SegmentSafetyFault as error:
            error.details["values_read_before_fault"] = dict(temperatures)
            raise
    affected = [
        {
            "motor_name": joint,
            "motor_id": _motor_id(bus, joint),
            "temperature_c": value,
            "limit_c": MAX_TEMPERATURE_C,
        }
        for joint, value in temperatures.items()
        if not 0 <= value <= MAX_TEMPERATURE_C
    ]
    if affected:
        raise SegmentSafetyFault(
            "temperature_limit_exceeded",
            {"stage": stage, "register": "Present_Temperature",
             "affected_motors": affected, "temperatures_c": temperatures},
        )
    return temperatures


def verify_prestart_temperatures(
        bus, *, limit_c_exclusive: int = PRESTART_MAX_TEMPERATURE_C_EXCLUSIVE,
) -> dict[str, int]:
    if not PRESTART_MAX_TEMPERATURE_C_EXCLUSIVE <= limit_c_exclusive <= MAX_TEMPERATURE_C:
        raise ValueError("invalid_prestart_temperature_limit")
    temperatures = _read_temperatures(bus, stage="prestart_temperature_check")
    affected = [
        {"motor_name": joint, "motor_id": _motor_id(bus, joint),
         "temperature_c": value,
         "required": f"<{limit_c_exclusive}C"}
        for joint, value in temperatures.items()
        if value >= limit_c_exclusive
    ]
    if affected:
        raise SegmentSafetyFault(
            "prestart_temperature_limit_exceeded",
            {"stage": "prestart_temperature_check",
             "register": "Present_Temperature", "affected_motors": affected,
             "temperatures_c": temperatures,
             "limit_c_exclusive": limit_c_exclusive},
        )
    return temperatures


class StrictWriteGate:
    """Block every write except the exact HOLD, motion, or Ctrl+C operation."""

    def __init__(self, bus):
        self.bus = bus
        self.original_sync_write = bus.sync_write
        self.mode = "closed"
        self.pending: tuple[str, dict[str, int | float], bool] | None = None
        self.last_motion_write_completed_ns: int | None = None
        self.counts = {"Goal_Position_raw_hold": 0, "Torque_Enable": 0,
                       "Goal_Position_motion": 0, "Goal_Position_ctrl_c_hold": 0,
                       "forbidden": 0}

        def denied(*_args, **_kwargs):
            self.counts["forbidden"] += 1
            raise RuntimeError("fixed_demo_forbidden_write")

        def checked(register, values, *args, **kwargs):
            pending, self.pending = self.pending, None
            normalized = kwargs.get("normalize", True)
            if args or kwargs.get("num_retry") != 0 or set(kwargs) - {"normalize", "num_retry"}:
                self.counts["forbidden"] += 1
                raise RuntimeError("fixed_demo_unexpected_write_signature")
            if (pending is None or register != pending[0] or dict(values) != pending[1]
                    or bool(normalized) != pending[2] or set(values) != set(JOINTS)):
                self.counts["forbidden"] += 1
                raise RuntimeError("fixed_demo_unarmed_sync_write")
            return self.original_sync_write(register, dict(values),
                                            normalize=normalized, num_retry=0)

        for name in ("write", "write_calibration", "configure_motors",
                     "enable_torque", "disable_torque"):
            if hasattr(bus, name):
                setattr(bus, name, denied)
        bus.sync_write = checked

    def _send(self, register: str, values: Mapping[str, int | float], normalize: bool) -> None:
        self.pending = (register, dict(values), normalize)
        try:
            self.bus.sync_write(register, dict(values), normalize=normalize, num_retry=0)
        finally:
            self.pending = None

    def atomic_goal_raw(self, values: Mapping[str, int]) -> None:
        if self.mode != "atomic" or self.counts["Goal_Position_raw_hold"]:
            raise RuntimeError("atomic_goal_not_allowed")
        self.counts["Goal_Position_raw_hold"] += 1
        self._send("Goal_Position", values, False)

    def atomic_torque_on(self) -> None:
        if (self.mode != "atomic" or self.counts["Torque_Enable"]
                or self.counts["Goal_Position_raw_hold"] != 1):
            raise RuntimeError("atomic_torque_not_allowed")
        self.counts["Torque_Enable"] += 1
        self._send("Torque_Enable", {joint: 1 for joint in JOINTS}, False)

    def arm_motion(self) -> None:
        if self.mode != "atomic":
            raise RuntimeError("motion_handoff_invalid")
        self.mode = "motion"

    def motion_goal(self, values: Mapping[str, float]) -> int:
        if self.mode != "motion":
            raise RuntimeError("motion_goal_not_armed")
        self.counts["Goal_Position_motion"] += 1
        command_monotonic_ns = time.monotonic_ns()
        self._send("Goal_Position", values, True)
        self.last_motion_write_completed_ns = time.monotonic_ns()
        return command_monotonic_ns

    def ctrl_c_hold(self, values: Mapping[str, int]) -> None:
        if self.mode != "motion" or self.counts["Goal_Position_ctrl_c_hold"]:
            raise RuntimeError("ctrl_c_hold_not_allowed")
        self.counts["Goal_Position_ctrl_c_hold"] += 1
        self._send("Goal_Position", values, False)

    def close(self) -> None:
        self.mode = "closed"
        self.pending = None


def establish_hold(
        bus, writes: StrictWriteGate, *,
        max_existing_goal_present_gap_raw: int =
        DEFAULT_ALREADY_ON_GOAL_PRESENT_RAW_TOLERANCE,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if (type(max_existing_goal_present_gap_raw) is not int
            or not 0 <= max_existing_goal_present_gap_raw
            <= MAX_SUPERVISED_HOLD_GOAL_PRESENT_RAW_TOLERANCE):
        raise ValueError("invalid_existing_goal_present_gap_tolerance")
    writes.mode = "atomic"
    present = _raw(bus, "Present_Position")
    torque = _raw(bus, "Torque_Enable")
    moving = _raw(bus, "Moving")
    if any(moving.values()):
        raise RuntimeError("moving_before_hold")
    if set(torque.values()) == {0}:
        writes.atomic_goal_raw(present)
        sleep(POST_GOAL_READ_DELAY_S)
        if _raw(bus, "Goal_Position", num_retry=POST_GOAL_READ_RETRIES) != present:
            raise RuntimeError("hold_goal_readback_mismatch")
        torque_after_goal = _raw(bus, "Torque_Enable", num_retry=POST_GOAL_READ_RETRIES)
        if set(torque_after_goal.values()) == {1}:
            hold_mode = "AUTO_ENABLED_BY_GOAL"
        elif set(torque_after_goal.values()) == {0}:
            writes.atomic_torque_on()
            hold_mode = "RESTORED_FROM_OFF"
        else:
            raise RuntimeError("mixed_torque_after_goal_write")
    elif set(torque.values()) == {1}:
        goal = _raw(bus, "Goal_Position")
        if any(abs(goal[joint] - present[joint])
               > max_existing_goal_present_gap_raw for joint in JOINTS):
            raise RuntimeError("already_on_goal_present_gap")
        hold_mode = "ALREADY_ON_NO_TOGGLE"
    else:
        raise RuntimeError("mixed_torque_state")
    expected_goal = _raw(bus, "Goal_Position")
    initial_present = _raw(bus, "Present_Position")
    started = monotonic()
    samples = 0
    while True:
        torque_now = _raw(bus, "Torque_Enable")
        goal_now = _raw(bus, "Goal_Position")
        present_now = _raw(bus, "Present_Position")
        moving_now = _raw(bus, "Moving")
        temperatures = _read_temperatures(bus, stage="hold_stability")
        if set(torque_now.values()) != {1} or goal_now != expected_goal or any(moving_now.values()):
            raise RuntimeError("hold_stability_state_changed")
        if any(abs(present_now[joint] - initial_present[joint]) > 2 for joint in JOINTS):
            raise RuntimeError("hold_stability_present_drift")
        samples += 1
        if monotonic() - started >= 3.0:
            break
        sleep(0.1)
    writes.arm_motion()
    return {
        "hold": hold_mode,
        "samples": samples,
        "writes": dict(writes.counts),
        "max_existing_goal_present_gap_raw": max_existing_goal_present_gap_raw,
    }


def calibrated_bounds(robot) -> dict[str, tuple[float, float]]:
    bus = getattr(robot, "bus", None)
    calibration = getattr(bus, "calibration", None)
    motors = getattr(bus, "motors", None)
    normalize = getattr(bus, "_normalize", None)
    if (not isinstance(calibration, Mapping)
            or not isinstance(motors, Mapping)
            or set(calibration) != set(JOINTS)
            or set(motors) != set(JOINTS)
            or not callable(normalize)):
        raise RuntimeError("stored_calibration_not_loaded")
    result: dict[str, tuple[float, float]] = {}
    motor_ids: set[int] = set()
    for joint in JOINTS:
        try:
            motor_id = getattr(motors[joint], "id")
            if type(motor_id) is not int or not 1 <= motor_id <= 253 or motor_id in motor_ids:
                raise ValueError("invalid_or_duplicate_motor_id")
            motor_ids.add(motor_id)
            item = calibration[joint]
            raw_min = item.range_min if hasattr(item, "range_min") else item["range_min"]
            raw_max = item.range_max if hasattr(item, "range_max") else item["range_max"]
            if (type(raw_min) is not int or type(raw_max) is not int
                    or not 0 <= raw_min < raw_max < 4096):
                raise ValueError("invalid_calibrated_raw_endpoints")
            low = float(normalize({motor_id: raw_min})[motor_id])
            high = float(normalize({motor_id: raw_max})[motor_id])
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"stored_calibration_invalid:{joint}") from error
        lower, upper = sorted((low, high))
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise RuntimeError(f"stored_calibration_invalid:{joint}")
        result[joint] = (lower, upper)
    return result


def ensure_in_bounds(values: Mapping[str, float], bounds: Mapping[str, tuple[float, float]]) -> None:
    violations = {joint: (values[joint], *bounds[joint]) for joint in JOINTS
                  if not bounds[joint][0] <= values[joint] <= bounds[joint][1]}
    if violations:
        raise RuntimeError(f"target_outside_calibrated_range:{violations}")


def clamp_to_calibrated_inner_range(values: Mapping[str, float],
                                    bounds: Mapping[str, tuple[float, float]],
                                    *, margin: float = 1.0) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    result = {}
    clamped = {}
    for joint in JOINTS:
        lower, upper = bounds[joint]
        inner_lower, inner_upper = lower + margin, upper - margin
        if inner_lower > inner_upper:
            raise RuntimeError(f"calibrated_range_too_narrow:{joint}")
        requested = float(values[joint])
        accepted = min(max(requested, inner_lower), inner_upper)
        result[joint] = accepted
        if accepted != requested:
            clamped[joint] = (requested, accepted)
    return result, clamped


class HardwareSession:
    def __init__(self, config, *, include_leader: bool):
        robot = config.raw["robot"]
        self.follower_port = Path(robot["follower_port"])
        self.follower_serial = robot["follower_serial"]
        self.leader_port = Path(robot["leader_port"])
        self.leader_serial = robot.get("leader_serial")
        self.include_leader = include_leader
        self.follower_lock = PortLock("follower")
        self.leader_lock = PortLock("leader")
        self.follower = None
        self.leader = None
        self.writes = None
        self.bounds = None

    def connect(self) -> None:
        self.follower_port, self.leader_port = resolve_robot_ports(
            self.follower_serial, self.leader_serial)
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

        self.follower_lock.acquire()
        if self.include_leader:
            self.leader_lock.acquire()
        try:
            self.follower = SO101Follower(SO101FollowerConfig(
                port=str(self.follower_port), id="so-101", cameras={}, use_degrees=True,
                max_relative_target=None, disable_torque_on_disconnect=False))
            self.writes = StrictWriteGate(self.follower.bus)
            self.follower.bus.connect(handshake=False)
            fcntl.ioctl(self.follower.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
            self.follower.configure = lambda: (_ for _ in ()).throw(RuntimeError("configure_forbidden"))
            self.bounds = calibrated_bounds(self.follower)
            if self.include_leader:
                self.leader = SO101Leader(SO101LeaderConfig(
                    port=str(self.leader_port), id="so-101"))
                StrictWriteGate(self.leader.bus)
                self.leader.bus.connect(handshake=False)
                fcntl.ioctl(self.leader.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
                self.leader.configure = lambda: (_ for _ in ()).throw(RuntimeError("configure_forbidden"))
                if not self.leader.bus.is_calibrated:
                    raise RuntimeError("leader_stored_calibration_not_loaded")
                if set(_raw(self.leader.bus, "Torque_Enable").values()) != {0}:
                    raise RuntimeError("leader_must_be_torque_off")
        except BaseException:
            self.close()
            raise

    def ctrl_c_hold(self) -> bool:
        if self.follower is not None and self.writes is not None and self.writes.mode == "motion":
            self.writes.ctrl_c_hold(_raw(self.follower.bus, "Present_Position"))
            return True
        return False

    def close(self) -> None:
        if self.writes is not None:
            self.writes.close()
        first_error = None
        try:
            for robot in (self.leader, self.follower):
                bus = getattr(robot, "bus", None)
                if bus is None:
                    continue
                try:
                    if getattr(bus, "is_connected", False):
                        bus.disconnect(disable_torque=False)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                finally:
                    serial = getattr(getattr(bus, "port_handler", None), "ser", None)
                    if serial is not None and hasattr(serial, "close"):
                        serial.close()
        finally:
            self.leader_lock.release()
            self.follower_lock.release()
        if first_error is not None:
            raise first_error


def _relative_target(leader: Mapping[str, float], leader_origin: Mapping[str, float],
                     follower_origin: Mapping[str, float]) -> dict[str, float]:
    return {joint: follower_origin[joint] + (leader[f"{joint}.pos"] - leader_origin[f"{joint}.pos"])
            for joint in JOINTS}


def _slew_target(requested: Mapping[str, float], previous: Mapping[str, float],
                 settings: DemoSettings) -> dict[str, float]:
    delta = {joint: requested[joint] - previous[joint] for joint in JOINTS}
    fraction = 1.0
    for joint, value in delta.items():
        per_cycle_velocity = settings.velocity[joint] * settings.control_period_s
        limit = min(settings.max_gripper_step if joint == "gripper" else settings.max_step_deg,
                    per_cycle_velocity)
        if abs(value) > limit:
            fraction = min(fraction, limit / abs(value))
    return {joint: previous[joint] + fraction * delta[joint] for joint in JOINTS}


def _validate_only_pose_names(only: Sequence[str] | None) -> tuple[str, ...] | None:
    if only is None:
        return None
    names = tuple(only)
    unknown = [name for name in names if name not in ALL_POSE_NAMES]
    if not names or unknown or len(set(names)) != len(names):
        raise _fail("teach_only_pose_names_invalid", list(names),
                    "unique known pose names",
                    "--only 뒤에 저장할 자세 이름을 중복 없이 지정해 주세요")
    return names


def teach(config, settings: DemoSettings, *, overwrite: bool = False, resume: bool = False,
          only: Sequence[str] | None = None, session_factory=HardwareSession) -> dict[str, Any]:
    only_names = _validate_only_pose_names(only)
    selected_modes = [name for name, enabled in (("overwrite", overwrite), ("resume", resume),
                                                  ("only", only_names is not None)) if enabled]
    if len(selected_modes) > 1:
        raise _fail("teach_mode_conflict", selected_modes, "one mode",
                    "--overwrite, --resume, --only 중 하나만 사용해 주세요")
    partial_path = partial_pose_path(settings.pose_path)
    if only_names is not None:
        if not settings.pose_path.is_file():
            raise _fail("pose_file_missing_for_teach_only", str(settings.pose_path),
                        "completed base 18-pose file",
                        "먼저 기본 teach 18개를 완료해 주세요")
        captured = load_pose_records(
            settings.pose_path, expected_serial=config.raw["robot"]["follower_serial"],
            required_names=POSE_NAMES)
        teach_names = only_names
        pose_index = 0
    elif settings.pose_path.exists() and not overwrite and not resume:
        raise _fail("pose_file_already_exists", str(settings.pose_path), "new path or --overwrite",
                    "기존 티칭값을 보존하려면 파일을 백업해 주세요")
    elif resume:
        if not partial_path.is_file():
            raise _fail("partial_pose_file_missing", str(partial_path), "saved partial pose file",
                        "처음부터 시작하려면 --resume을 빼 주세요")
        captured = load_partial_pose_file(partial_path,
                                          expected_serial=config.raw["robot"]["follower_serial"])
        teach_names = POSE_NAMES
        pose_index = len(captured)
    else:
        if partial_path.exists() and not overwrite:
            raise _fail("partial_pose_file_exists", str(partial_path), "--resume or --overwrite",
                        "이어서 하려면 --resume을 사용해 주세요")
        if overwrite and partial_path.exists():
            partial_path.unlink()
        captured = {}
        teach_names = POSE_NAMES
        pose_index = 0
    session = session_factory(config, include_leader=True)
    old_terminal = None
    stopping = False
    saved_this_run: list[str] = []

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    old_signals = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        session.connect()
        hold = establish_hold(session.follower.bus, session.writes)
        leader_origin = dict(session.leader.get_action())
        follower_origin = _positions(session.follower.bus)
        previous = dict(follower_origin)
        print("티칭 시작: Enter=현재 자세 저장, q=중단, Ctrl+C=현재 위치 유지")
        print("그리퍼 값은 gripper_open/gripper_closed_A/B/C 자세에서만 의미가 있으며, "
              "나머지 자세는 팔 5축만 재생합니다.")
        if sys.stdin.isatty():
            old_terminal = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        while pose_index < len(teach_names) and not stopping:
            pose_name = teach_names[pose_index]
            leader_now = dict(session.leader.get_action())
            requested_unclamped = _relative_target(leader_now, leader_origin, follower_origin)
            requested, clamped = clamp_to_calibrated_inner_range(
                requested_unclamped, session.bounds, margin=1.0)
            emitted = _slew_target(requested, previous, settings)
            ensure_in_bounds(emitted, session.bounds)
            if any(abs(emitted[j] - previous[j]) >
                   (settings.max_gripper_step if j == "gripper" else settings.max_step_deg) + 1e-9
                   for j in JOINTS):
                raise RuntimeError("two_degree_slew_violation")
            if any(abs(emitted[j] - previous[j]) > 1e-9 for j in JOINTS):
                session.writes.motion_goal(emitted)
                previous = emitted
            clamp_note = f" CLAMP={','.join(clamped)}" if clamped else ""
            if pose_name == "gripper_closed":
                pose_note = "기존 호환용(run 미사용)"
            else:
                pose_note = "그리퍼 포함" if pose_name in GRIPPER_POSES else "팔 5축만"
            print(f"\r[{pose_index + 1}/{len(teach_names)}] {pose_name} ({pose_note}) 자세에서 Enter{clamp_note} ",
                  end="", flush=True)
            readable, _, _ = select.select([sys.stdin], [], [], settings.control_period_s)
            if not readable:
                continue
            key = sys.stdin.read(1)
            if key.lower() == "q":
                raise KeyboardInterrupt
            if key not in ("\n", "\r"):
                continue
            if any(_raw(session.follower.bus, "Moving").values()):
                print("\n아직 움직이는 중입니다. 정지 후 다시 Enter를 누르세요.")
                continue
            raw_present = _raw(session.follower.bus, "Present_Position", num_retry=3)
            raw_goal = _raw(session.follower.bus, "Goal_Position", num_retry=3)
            if any(abs(raw_goal[j] - raw_present[j]) > TEACH_GOAL_PRESENT_RAW_TOLERANCE
                   for j in JOINTS):
                print("\nGoal-Present가 아직 수렴하지 않았습니다. 잠시 뒤 다시 Enter를 누르세요.")
                continue
            captured[pose_name] = {
                "position": _positions(session.follower.bus),
                "present_raw": raw_present,
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            save_path = settings.pose_path if only_names is not None else partial_path
            save_pose_file(save_path, follower_serial=session.follower_serial, poses=captured)
            saved_this_run.append(pose_name)
            pose_index += 1
            save_kind = "pose file" if only_names is not None else "partial"
            print(f"\n저장: {pose_name} ({save_kind} {pose_index}/{len(teach_names)})")
        if stopping:
            raise KeyboardInterrupt
        if only_names is None:
            save_pose_file(settings.pose_path, follower_serial=session.follower_serial, poses=captured)
            partial_path.unlink(missing_ok=True)
        return {"status": "TEACH_ONLY_COMPLETE" if only_names is not None else "TEACH_COMPLETE",
                "pose_file": str(settings.pose_path), "pose_count": len(captured),
                "saved_pose_names": saved_this_run, "hold": hold,
                "write_counts": dict(session.writes.counts)}
    except KeyboardInterrupt:
        held = session.ctrl_c_hold()
        code = "teach_stopped_current_position_held" if held else "teach_stopped_before_motion_arm"
        remedy = (("저장된 자세는 유지됩니다. 주변과 상태 확인 후 남은 자세만 teach --only로 다시 실행해 주세요"
                   if only_names is not None else
                   "주변과 follower Torque/Goal 상태를 확인한 뒤 teach --resume으로 이어가 주세요")
                  if (only_names is not None or partial_path.exists()) else
                  "저장된 pose가 없습니다. 주변과 Torque/Goal 상태 확인 후 teach를 다시 실행해 주세요")
        progress = len(saved_this_run) if only_names is not None else len(captured)
        raise _fail(code, progress, str(len(teach_names)),
                    remedy)
    finally:
        if old_terminal is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_terminal)
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
        session.close()


def _gripper_only(current: Mapping[str, float], taught: Mapping[str, float]) -> dict[str, float]:
    result = dict(current)
    result["gripper"] = taught["gripper"]
    return result


def _arm_only(current: Mapping[str, float], taught: Mapping[str, float]) -> dict[str, float]:
    result = dict(taught)
    result["gripper"] = current["gripper"]
    return result


def target_for_stage(stage: str, current: Mapping[str, float],
                     taught: Mapping[str, float]) -> dict[str, float]:
    return (_gripper_only(current, taught) if stage in GRIPPER_POSES
            else _arm_only(current, taught))


def _relieve_blocked_gripper(session: HardwareSession, *, stage: str,
                            start: Mapping[str, float], target: Mapping[str, float],
                            settings: DemoSettings,
                            sleep: Callable[[float], None]) -> tuple[dict[str, float], dict[str, Any] | None]:
    if stage not in TYPE_GRIPPER_POSES:
        return dict(target), None
    actual = float(_read_motor_register(
        session.follower.bus, "Present_Position", "gripper", normalize=True,
        num_retry=0, stage=stage))
    if not math.isfinite(actual):
        raise SegmentSafetyFault(
            "nonfinite_gripper_position",
            {"stage": stage, "motor_name": "gripper",
             "motor_id": _motor_id(session.follower.bus, "gripper"),
             "register": "Present_Position", "register_value": actual},
        )
    commanded_travel = target["gripper"] - start["gripper"]
    remaining = target["gripper"] - actual
    blocked_before_target = (commanded_travel * remaining > 0
                             and abs(remaining) > settings.max_gripper_step)
    if not blocked_before_target:
        return dict(target), None

    direction = 1.0 if commanded_travel > 0 else -1.0
    relief_gripper = actual + direction * min(
        GRIPPER_CONTACT_RELIEF_BIAS, abs(remaining))
    current = _positions(session.follower.bus)
    relieved = dict(current)
    relieved["gripper"] = relief_gripper
    ensure_in_bounds(relieved, session.bounds)
    session.writes.motion_goal(relieved)
    sleep(settings.control_period_s)
    return relieved, {
        "stage": stage,
        "motor_name": "gripper",
        "motor_id": _motor_id(session.follower.bus, "gripper"),
        "commanded_target": target["gripper"],
        "present_at_contact": actual,
        "remaining_to_original_target": remaining,
        "relieved_goal": relief_gripper,
        "relief_bias": GRIPPER_CONTACT_RELIEF_BIAS,
    }


def _record_motion_cycle(session: HardwareSession, recorder: MotionTraceRecorder | None, *,
                         stage: str, profile: str, sequence: int,
                         scheduled_offset_s: float, planned_monotonic_ns: int,
                         command_monotonic_ns: int, write_completed_monotonic_ns: int,
                         command_position: Mapping[str, float],
                         present_raw: Mapping[str, int] | None = None,
                         goal_raw: Mapping[str, int] | None = None,
                         torque: Mapping[str, int] | None = None,
                         moving: Mapping[str, int] | None = None,
                         feedback_monotonic_ns: int | None = None,
                         temperatures_c: Mapping[str, int] | None = None,
                         goal_source: str = "register_readback",
                         read_full_feedback: bool = True) -> None:
    if recorder is None:
        return
    if read_full_feedback:
        present_raw = _raw(session.follower.bus, "Present_Position")
        goal_raw = _raw(session.follower.bus, "Goal_Position")
        torque = _raw(session.follower.bus, "Torque_Enable")
        moving = _raw(session.follower.bus, "Moving")
        feedback_monotonic_ns = time.monotonic_ns()
    present_position = (None if present_raw is None else
                        _normalize_raw_positions(session.follower.bus, present_raw))
    if goal_raw is not None:
        goal_position = _normalize_raw_positions(session.follower.bus, goal_raw)
    elif present_raw is not None:
        goal_position = {joint: float(command_position[joint]) for joint in JOINTS}
    else:
        goal_position = None
    recorder.record(
        stage=stage, profile=profile, sequence=sequence,
        scheduled_offset_s=scheduled_offset_s,
        planned_monotonic_ns=planned_monotonic_ns,
        command_monotonic_ns=command_monotonic_ns,
        write_completed_monotonic_ns=write_completed_monotonic_ns,
        command_position=command_position,
        present_position=present_position,
        goal_position=goal_position,
        present_raw=present_raw, goal_raw=goal_raw, torque_enable=torque,
        moving=moving, feedback_monotonic_ns=feedback_monotonic_ns,
        temperatures_c=temperatures_c, goal_source=goal_source,
    )


def _execute_original_commands(session: HardwareSession, start: Mapping[str, float],
                               target: Mapping[str, float], settings: DemoSettings, *,
                               stage: str, recorder: MotionTraceRecorder | None,
                               sleep: Callable[[float], None],
                               monotonic: Callable[[], float],
                               stop_requested: Callable[[], bool]) -> None:
    planned_started_ns: int | None = None
    for sequence, point in enumerate(smooth_interpolation(start, target, settings), start=1):
        if stop_requested():
            raise KeyboardInterrupt
        fallback_ns = int(monotonic() * 1_000_000_000)
        command_ns = session.writes.motion_goal(point)
        actual_ns = command_ns if isinstance(command_ns, int) else fallback_ns
        completed_ns = getattr(session.writes, "last_motion_write_completed_ns", None)
        if not isinstance(completed_ns, int):
            completed_ns = actual_ns
        if planned_started_ns is None:
            planned_started_ns = actual_ns
        scheduled_offset_s = (sequence - 1) * settings.control_period_s
        _record_motion_cycle(
            session, recorder, stage=stage, profile="original", sequence=sequence,
            scheduled_offset_s=scheduled_offset_s,
            planned_monotonic_ns=(planned_started_ns
                                  + round(scheduled_offset_s * 1_000_000_000)),
            command_monotonic_ns=actual_ns,
            write_completed_monotonic_ns=completed_ns,
            command_position=point,
        )
        sleep(settings.control_period_s)


def _execute_time_based_commands(session: HardwareSession, start: Mapping[str, float],
                                 target: Mapping[str, float], settings: DemoSettings, *,
                                 stage: str, recorder: MotionTraceRecorder | None,
                                 sleep: Callable[[float], None],
                                 monotonic: Callable[[], float],
                                 stop_requested: Callable[[], bool]) -> None:
    # Validate the same travel and 2-degree contract as the original generator first.
    smooth_interpolation(start, target, settings, profile="quintic")
    delta = {joint: float(target[joint]) - float(start[joint]) for joint in JOINTS}
    duration = trajectory_duration(delta, settings, profile="quintic")
    started = monotonic()
    deadline = started + settings.control_period_s
    next_feedback = started
    next_safety = started
    previous = {joint: float(start[joint]) for joint in JOINTS}
    sequence = 0
    while True:
        if stop_requested():
            raise KeyboardInterrupt
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(remaining)
        now = monotonic()
        elapsed = max(0.0, now - started)
        requested = trajectory_position(start, delta, elapsed / duration, profile="quintic")
        point = _hard_cap_synchronized(previous, requested, settings)
        sequence += 1
        fallback_ns = int(now * 1_000_000_000)
        command_ns = session.writes.motion_goal(point)
        actual_ns = command_ns if isinstance(command_ns, int) else fallback_ns
        completed_ns = getattr(session.writes, "last_motion_write_completed_ns", None)
        if not isinstance(completed_ns, int):
            completed_ns = actual_ns
        final_command = (elapsed >= duration
                         and all(abs(point[joint] - float(target[joint])) <= 1e-9
                                 for joint in JOINTS))
        present_raw = None
        goal_raw = None
        moving = None
        feedback_ns = None
        if recorder is not None and (sequence == 1 or now >= next_feedback or final_command):
            present_raw = _raw(session.follower.bus, "Present_Position")
            goal_raw = _raw(session.follower.bus, "Goal_Position")
            moving = _raw(session.follower.bus, "Moving")
            feedback_ns = int(monotonic() * 1_000_000_000)
            while next_feedback <= monotonic():
                next_feedback += SMOOTH_FEEDBACK_PERIOD_S
        torque = None
        temperatures = None
        if sequence == 1 or now >= next_safety or final_command:
            torque = _raw(session.follower.bus, "Torque_Enable")
            if set(torque.values()) != {1}:
                raise SegmentSafetyFault(
                    "torque_changed_during_smooth_motion",
                    {"stage": stage, "sequence": sequence, "torque_enable": torque},
                )
            temperatures = _read_temperatures(
                session.follower.bus, stage=f"{stage}:smooth_safety")
            while next_safety <= monotonic():
                next_safety += SMOOTH_SAFETY_PERIOD_S
        _record_motion_cycle(
            session, recorder, stage=stage, profile="smooth_quintic_absolute",
            sequence=sequence,
            scheduled_offset_s=deadline - started,
            planned_monotonic_ns=round(deadline * 1_000_000_000),
            command_monotonic_ns=actual_ns,
            write_completed_monotonic_ns=completed_ns,
            command_position=point,
            present_raw=present_raw, goal_raw=goal_raw, torque=torque, moving=moving,
            feedback_monotonic_ns=feedback_ns, temperatures_c=temperatures,
            goal_source="sparse_register_readback",
            read_full_feedback=False,
        )
        previous = point
        if final_command:
            break
        deadline += settings.control_period_s
        after_cycle = monotonic()
        if deadline <= after_cycle:
            # Skip expired schedule slots instead of bursting stale waypoints back-to-back.
            next_tick = math.floor((after_cycle - started) / settings.control_period_s) + 1
            deadline = started + next_tick * settings.control_period_s


def execute_segment(session: HardwareSession, target: Mapping[str, float], settings: DemoSettings,
                    *, stage: str, motion_profile: str = "original",
                    recorder: MotionTraceRecorder | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    monotonic: Callable[[], float] = time.monotonic,
                    stop_requested: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    if motion_profile not in MOTION_PROFILES:
        raise ValueError(f"motion_profile_invalid:{motion_profile}")
    ensure_in_bounds(target, session.bounds)
    start = _positions(session.follower.bus)
    if motion_profile == "original":
        _execute_original_commands(
            session, start, target, settings, stage=stage, recorder=recorder,
            sleep=sleep, monotonic=monotonic, stop_requested=stop_requested)
    else:
        _execute_time_based_commands(
            session, start, target, settings, stage=stage, recorder=recorder,
            sleep=sleep, monotonic=monotonic, stop_requested=stop_requested)
    transit_settle = stage in TRANSIT_SETTLE_STAGE_NAMES
    settle_tolerance = dict(settings.settle_tolerance)
    settle_dwell_s = 0.0
    if transit_settle:
        settle_tolerance["elbow_flex"] = TRANSIT_ELBOW_SETTLE_TOLERANCE_DEG
        settle_dwell_s = TRANSIT_SETTLE_DWELL_S
    settle_started = monotonic()
    deadline = settle_started + settings.settle_timeout_s
    stable_since: float | None = None
    while True:
        if stop_requested():
            raise KeyboardInterrupt
        present = _positions(session.follower.bus)
        moving = _raw(session.follower.bus, "Moving")
        sampled_at = monotonic()
        within_tolerance = all(
            abs(present[joint] - target[joint]) <= settle_tolerance[joint]
            for joint in JOINTS)
        if not any(moving.values()) and within_tolerance:
            if stable_since is None:
                stable_since = sampled_at
            if sampled_at - stable_since >= settle_dwell_s:
                break
        else:
            stable_since = None
        if sampled_at >= deadline:
            goal = _normalized_positions(session.follower.bus, "Goal_Position")
            raise SegmentSafetyFault(
                "segment_settle_timeout",
                {"stage": stage, "motion_profile": motion_profile,
                 "settle_policy": "transit_relaxed" if transit_settle else "precise",
                 "settle_tolerance": settle_tolerance,
                 "settle_dwell_s": settle_dwell_s,
                 "stable_duration_s": (0.0 if stable_since is None else
                                       sampled_at - stable_since),
                 "target": dict(target), "present": present, "goal": goal,
                 "goal_present_error": {
                     joint: goal[joint] - present[joint] for joint in JOINTS},
                 "target_present_error": {
                     joint: float(target[joint]) - present[joint] for joint in JOINTS},
                 "moving": moving, "settle_timeout_s": settings.settle_timeout_s},
            )
        sleep(settings.control_period_s)
    final_target, contact_relief = _relieve_blocked_gripper(
        session, stage=stage, start=start, target=target, settings=settings, sleep=sleep)
    if set(_raw(session.follower.bus, "Torque_Enable").values()) != {1}:
        raise RuntimeError("torque_changed_during_demo")
    temperatures = _read_temperatures(session.follower.bus, stage=stage)
    return {"final_target": final_target, "contact_relief": contact_relief,
            "temperatures_c": temperatures,
            "settle_policy": "transit_relaxed" if transit_settle else "precise",
            "settle_tolerance": settle_tolerance,
            "settle_dwell_s": settle_dwell_s,
            "settle_duration_s": monotonic() - settle_started}


def run_free_space_motion_test(
        config, settings: DemoSettings, *, from_pose: str | None, to_pose: str,
        from_current: bool = False, motion_profile: str, log_path: Path, step: bool,
        stable_hold_gap_raw: int = DEFAULT_ALREADY_ON_GOAL_PRESENT_RAW_TOLERANCE,
        control_hz: int | None = None,
        free_space_velocity: int | None = None,
        session_factory=HardwareSession,
        install_signal_handlers: bool = True,
) -> dict[str, Any]:
    if not step:
        raise _fail("motion_test_requires_step", False, "--step",
                    "짧은 free-space 시험은 현장 확인 후 단계 실행만 허용합니다")
    if (type(stable_hold_gap_raw) is not int
            or not DEFAULT_ALREADY_ON_GOAL_PRESENT_RAW_TOLERANCE
            <= stable_hold_gap_raw <= MAX_SUPERVISED_HOLD_GOAL_PRESENT_RAW_TOLERANCE):
        raise _fail(
            "motion_test_stable_hold_gap_invalid", stable_hold_gap_raw,
            (f"{DEFAULT_ALREADY_ON_GOAL_PRESENT_RAW_TOLERANCE}.."
             f"{MAX_SUPERVISED_HOLD_GOAL_PRESENT_RAW_TOLERANCE} raw"),
            "기본 10 raw를 쓰거나 감독 시험에서 명시적으로 최대 60 raw를 지정해 주세요",
        )
    if motion_profile not in MOTION_PROFILES:
        raise _fail("motion_profile_invalid", motion_profile, "original or smooth",
                    "A/B 비교할 profile을 지정해 주세요")
    if motion_profile == "original" and (control_hz is not None or
                                          free_space_velocity is not None):
        raise _fail(
            "original_profile_options_forbidden",
            {"control_hz": control_hz, "free_space_velocity": free_space_velocity},
            "no smooth-only options with original",
            "ORIGINAL은 기존 10Hz·20°/s 그대로 실행해 주세요",
        )
    selected_control_hz = (DEFAULT_SMOOTH_CONTROL_HZ if control_hz is None
                           else control_hz)
    selected_free_space_velocity = (
        DEFAULT_FREE_SPACE_VELOCITY if free_space_velocity is None
        else free_space_velocity
    )
    if selected_control_hz not in SMOOTH_CONTROL_HZ_CHOICES:
        raise _fail("smooth_control_hz_invalid", selected_control_hz,
                    str(SMOOTH_CONTROL_HZ_CHOICES),
                    "30, 40, 50Hz 중 하나를 지정해 주세요")
    if selected_free_space_velocity not in FREE_SPACE_VELOCITY_CHOICES:
        raise _fail("free_space_velocity_invalid", selected_free_space_velocity,
                    str(FREE_SPACE_VELOCITY_CHOICES),
                    "20, 30, 40°/s 중 하나를 지정해 주세요")
    if from_current == (from_pose is not None):
        raise _fail("motion_test_start_mode_invalid", {
            "from_pose": from_pose, "from_current": from_current},
            "exactly one of from_pose or from_current",
            "현재 자세 시작은 --from-current를 사용해 주세요")
    if (to_pose not in ALL_POSE_NAMES or to_pose in GRIPPER_POSES
            or (from_pose is not None and (
                from_pose not in ALL_POSE_NAMES or from_pose in GRIPPER_POSES
                or from_pose == to_pose))):
        raise _fail("motion_test_pose_invalid", [from_pose, to_pose],
                    "current or arm pose start, and a different arm pose target",
                    "충돌 없는 짧은 arm-only 종료 자세를 지정해 주세요")
    poses = load_pose_file(settings.pose_path,
                           expected_serial=config.raw["robot"]["follower_serial"])
    recorder = MotionTraceRecorder(log_path)
    session = session_factory(config, include_leader=False)
    motion_armed = False
    old_signals = {}
    stopping = False
    if motion_profile == "original":
        execution_settings = replace(settings, control_period_s=ORIGINAL_CONTROL_PERIOD_S)
    else:
        smooth_velocity = dict(settings.velocity)
        smooth_velocity.update({joint: float(selected_free_space_velocity)
                                for joint in ARM_JOINTS})
        execution_settings = replace(
            settings,
            control_period_s=1.0 / selected_control_hz,
            velocity=smooth_velocity,
        )
    start_label = "CURRENT" if from_current else str(from_pose)

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    if install_signal_handlers:
        if threading.current_thread() is not threading.main_thread():
            recorder.close()
            raise RuntimeError("signal_handlers_require_main_thread")
        old_signals = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        print(json.dumps({
            "status": "FREE_SPACE_MOTION_TEST_PLAN",
            "from_pose": start_label, "to_pose": to_pose,
            "start_pose_gate": ("fresh_current_no_taught_pose_match" if from_current else
                                "taught_pose_within_1deg"),
            "motion_profile": motion_profile, "log_path": str(log_path),
            "arm_only": True, "gripper_goal_change": False,
            "two_degree_hard_cap": settings.max_step_deg,
            "control_period_s": execution_settings.control_period_s,
            "control_hz": (10 if motion_profile == "original" else selected_control_hz),
            "trajectory_curve": ("cubic" if motion_profile == "original" else "quintic"),
            "deadline_mode": ("relative_sleep" if motion_profile == "original" else
                              "absolute_deadline_skip_expired_ticks"),
            "arm_velocity_limit_deg_s": (
                settings.velocity["shoulder_pan"] if motion_profile == "original" else
                selected_free_space_velocity),
            "feedback_period_s": (
                execution_settings.control_period_s if motion_profile == "original" else
                SMOOTH_FEEDBACK_PERIOD_S),
            "temperature_overload_period_s": (
                execution_settings.control_period_s if motion_profile == "original" else
                SMOOTH_SAFETY_PERIOD_S),
            "stable_hold_gap_raw": stable_hold_gap_raw,
        }, ensure_ascii=False, indent=2))
        input("팔 지지·free-space·즉시 정지 준비와 시작 자세를 확인했으면 Enter: ")
        session.connect()
        prestart_temperatures = verify_prestart_temperatures(session.follower.bus)
        hold = establish_hold(
            session.follower.bus, session.writes,
            max_existing_goal_present_gap_raw=stable_hold_gap_raw,
        )
        motion_armed = True
        current = _positions(session.follower.bus)
        moving = _raw(session.follower.bus, "Moving")
        start_error = ({joint: 0.0 for joint in ARM_JOINTS} if from_current else {
            joint: current[joint] - float(poses[str(from_pose)][joint])
            for joint in ARM_JOINTS
        })
        if any(moving.values()):
            raise SegmentSafetyFault(
                "motion_test_start_moving",
                {"stage": from_pose, "moving": moving, "start_error": start_error})
        if not from_current and any(abs(value) > 1.0 for value in start_error.values()):
            raise SegmentSafetyFault(
                "motion_test_start_pose_mismatch",
                {"stage": from_pose, "start_error": start_error,
                 "required_abs_max_deg": 1.0})
        target = _arm_only(current, poses[to_pose])
        input(f"{start_label} → {to_pose} 한 구간만 실행합니다. 주변 clear면 Enter: ")
        segment = execute_segment(
            session, target, execution_settings,
            stage=f"motion_test:{start_label}->{to_pose}",
            motion_profile=motion_profile, recorder=recorder,
            stop_requested=lambda: stopping,
        )
        actual_analysis = load_and_analyze_motion_log(log_path)
        encoder_motion_confirmed = (
            actual_analysis.get("raw_feedback_available") is True
            and any(abs(actual_analysis["joints"][joint]["present_raw_net_delta"]) >= 1
                    for joint in ARM_JOINTS)
        )
        return {
            "status": "FREE_SPACE_MOTION_TEST_EXTERNAL_CONFIRMATION_REQUIRED",
            "from_pose": start_label, "to_pose": to_pose,
            "fresh_current_start": current if from_current else None,
            "start_pose_gate": ("fresh_current_no_taught_pose_match" if from_current else
                                "taught_pose_within_1deg"),
            "motion_profile": motion_profile,
            "control_hz": (10 if motion_profile == "original" else selected_control_hz),
            "trajectory_curve": ("cubic" if motion_profile == "original" else "quintic"),
            "arm_velocity_limit_deg_s": (
                settings.velocity["shoulder_pan"] if motion_profile == "original" else
                selected_free_space_velocity),
            "log_path": str(log_path.resolve()), "log_samples": recorder.count,
            "prestart_temperatures_c": prestart_temperatures,
            "final_temperatures_c": segment["temperatures_c"],
            "hold": hold, "write_counts": dict(session.writes.counts),
            "actual_log_analysis": actual_analysis,
            "encoder_motion_confirmed": encoder_motion_confirmed,
            "encoder_motion_minimum_raw_counts": 1,
            "external_link_motion_confirmation": "REQUIRED_FROM_USER_VIDEO",
            "torque_gain_changed": False, "ik_changed": False,
            "calibration_changed": False,
        }
    except KeyboardInterrupt:
        held = session.ctrl_c_hold() if motion_armed else False
        raise _fail(
            "motion_test_stopped_current_position_held" if held else
            "motion_test_stopped_before_motion_arm",
            {"current_position_hold": held, "log_path": str(log_path),
             "log_samples": recorder.count},
            "one supervised free-space segment",
            "현재 물리 상태를 확인하고 자동 재실행하지 마세요",
        )
    except SorterError:
        raise
    except Exception as error:
        held = False
        hold_error = None
        if motion_armed:
            try:
                held = session.ctrl_c_hold()
            except Exception as current_hold_error:
                hold_error = f"{type(current_hold_error).__name__}: {current_hold_error}"
        details = (dict(error.details) if isinstance(error, SegmentSafetyFault) else
                   {"exception_type": type(error).__name__, "driver_error": str(error)})
        details.update({"current_position_hold": held, "hold_error": hold_error,
                        "log_path": str(log_path), "log_samples": recorder.count})
        raise _fail(
            "motion_test_fault_current_position_held" if held else
            "motion_test_fault_hold_not_completed",
            details, "one fault-free free-space segment",
            "현재 물리 상태와 로그를 확인하고 자동 재실행하지 마세요",
        ) from error
    finally:
        recorder.close()
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
        session.close()


def run_demo(config, settings: DemoSettings, *, dry_run: bool, step: bool,
             auto: bool = False,
             warm_start_once: bool = False,
             motion_profile: str = DEFAULT_DEMO_MOTION_PROFILE,
             rules: Mapping[str, str] | None = None,
             snapshot_loader: Callable[[str], dict[str, Any]] = fetch_snapshot,
             session_factory=HardwareSession,
             progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
             stop_requested: Callable[[], bool] = lambda: False,
             install_signal_handlers: bool = True) -> dict[str, Any]:
    if step and auto:
        raise _fail("run_mode_conflict", ["step", "auto"], "one execution mode",
                    "--step 또는 --auto 중 하나만 사용해 주세요")
    if not dry_run and not (step or auto):
        raise _fail("run_execution_mode_required", None, "--step or --auto",
                    "감독 실행은 --step, 연속 실행은 --auto를 사용해 주세요")
    if warm_start_once and not step:
        raise _fail("warm_start_requires_supervised_step", True, "--step",
                    "50–51°C 1회 시작은 터미널 --step에서만 사용해 주세요")
    if motion_profile not in MOTION_PROFILES:
        raise _fail("motion_profile_invalid", motion_profile, "original or smooth",
                    "smooth를 사용하거나 기존 방식은 --profile original로 선택해 주세요")
    if motion_profile == "smooth":
        smooth_velocity = dict(settings.velocity)
        smooth_velocity.update({joint: float(DEFAULT_FREE_SPACE_VELOCITY)
                                for joint in ARM_JOINTS})
        execution_settings = replace(
            settings,
            control_period_s=1.0 / DEFAULT_SMOOTH_CONTROL_HZ,
            velocity=smooth_velocity,
        )
    else:
        execution_settings = settings
    prestart_temperature_limit = (
        WARM_START_MAX_TEMPERATURE_C_EXCLUSIVE
        if warm_start_once else PRESTART_MAX_TEMPERATURE_C_EXCLUSIVE
    )
    active_rules = (parse_destination_rules(None, settings.destinations) if rules is None else
                    parse_destination_rules([f"{label}={color}" for label, color in rules.items()], {}))
    snapshot = snapshot_loader(settings.snapshot_url)
    marker_layout = verify_expected_marker_layout(snapshot, settings)
    routes, ignored = routes_from_snapshot(snapshot, settings, active_rules)
    stages = stage_names_for_routes(routes)
    result = {
        "status": "FIXED_DEMO_PLAN" if dry_run else "FIXED_DEMO_COMPLETE",
        "frame_sequence": snapshot.get("sequence"),
        "frame_age_s": snapshot.get("frame_age_s"),
        "slot_roi_status": settings.roi_status,
        "destination_rules": active_rules,
        "routes": [route.as_dict() for route in routes],
        "ignored_markers": ignored,
        "marker_layout_preflight": marker_layout,
        "stages": stages,
        "yolo": {"optional": True, "detections_visible_in_camera_ui": True,
                 "detection_count": len(snapshot.get("detections", []))},
        "serial_open": 0,
        "motor_command": 0,
        "prestart_temperature_limit_c_exclusive": prestart_temperature_limit,
        "warm_start_once": warm_start_once,
        "motion_profile": motion_profile,
        "control_hz": (DEFAULT_SMOOTH_CONTROL_HZ if motion_profile == "smooth" else
                       round(1.0 / execution_settings.control_period_s)),
        "trajectory_curve": "quintic" if motion_profile == "smooth" else "cubic",
        "deadline_mode": ("absolute_deadline_skip_expired_ticks"
                          if motion_profile == "smooth" else "relative_sleep"),
        "arm_velocity_limit_deg_s": (
            DEFAULT_FREE_SPACE_VELOCITY if motion_profile == "smooth" else
            execution_settings.velocity["shoulder_pan"]),
    }
    if dry_run:
        return result
    if settings.roi_status != "verified" and auto:
        raise _fail("auto_requires_verified_slot_roi", settings.roi_status, "verified",
                    "dry-run에서 칸 ROI를 검증한 뒤 설정 상태를 갱신해 주세요")
    poses = load_pose_file(settings.pose_path,
                           expected_serial=config.raw["robot"]["follower_serial"])
    session = session_factory(config, include_leader=False)
    stopping = False
    active_stage = None
    completed_stages: list[str] = []
    contact_reliefs: list[dict[str, Any]] = []
    motion_armed = False
    prestart_temperatures = None
    latest_temperatures = None

    def progress(payload: Mapping[str, Any]) -> None:
        if progress_callback is not None:
            progress_callback(dict(payload))

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    old_signals = {}
    if install_signal_handlers:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("signal_handlers_require_main_thread")
        old_signals = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if step:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            input("위 ID→칸→바구니 계획과 주변 clear를 확인했으면 Enter: ")
        if stop_requested():
            raise KeyboardInterrupt
        session.connect()
        prestart_temperatures = verify_prestart_temperatures(
            session.follower.bus,
            limit_c_exclusive=prestart_temperature_limit,
        )
        latest_temperatures = prestart_temperatures
        progress({"status": "PRESTART_PASS", "temperatures_c": prestart_temperatures,
                  "marker_layout": marker_layout, "motion_profile": motion_profile})
        if stop_requested():
            raise KeyboardInterrupt
        hold = establish_hold(session.follower.bus, session.writes)
        motion_armed = True
        current = _positions(session.follower.bus)
        for index, stage in enumerate(stages, start=1):
            active_stage = stage
            if stopping or stop_requested():
                raise KeyboardInterrupt
            target = target_for_stage(stage, current, poses[stage])
            if step:
                input(f"[{index}/{len(stages)}] {stage} 실행: 주변을 확인하고 Enter ")
            if stopping or stop_requested():
                raise KeyboardInterrupt
            progress({"status": "RUNNING", "stage_index": index,
                      "stage_count": len(stages), "stage": stage,
                      "temperatures_c": latest_temperatures,
                      "motion_profile": motion_profile})
            segment = execute_segment(session, target, execution_settings, stage=stage,
                                      motion_profile=motion_profile,
                                      stop_requested=lambda: stopping or stop_requested())
            current = dict(segment["final_target"])
            latest_temperatures = segment["temperatures_c"]
            if segment["contact_relief"] is not None:
                contact_reliefs.append(segment["contact_relief"])
            completed_stages.append(stage)
        result.update({"hold": hold, "serial_open": 1,
                       "prestart_temperatures_c": prestart_temperatures,
                       "final_temperatures_c": latest_temperatures,
                       "motor_command": session.writes.counts["Goal_Position_motion"],
                       "write_counts": dict(session.writes.counts),
                       "contact_reliefs": contact_reliefs})
        progress({"status": "COMPLETE", "stage_index": len(stages),
                  "stage_count": len(stages), "stage": stages[-1],
                  "temperatures_c": latest_temperatures,
                  "motion_profile": motion_profile})
        return result
    except KeyboardInterrupt:
        connected = session.follower is not None and session.writes is not None
        if connected and motion_armed:
            held = session.ctrl_c_hold()
            code = "demo_stopped_current_position_held" if held else "demo_stopped_before_motion_arm"
            raise _fail(code, True, "supervised completion",
                        "follower Torque/Goal 상태와 주변을 확인해 주세요")
        if connected:
            raise _fail("demo_stopped_before_motion_arm", True, "prestart completion",
                        "로봇 동작 명령 전 중단됐습니다. 상태를 확인해 주세요")
        raise _fail("demo_stopped_before_robot_connect", True, "plan confirmation",
                    "로봇 명령은 없었으며 필요할 때 다시 실행해 주세요")
    except Exception as error:
        connected = session.follower is not None and session.writes is not None
        hold_error = None
        held = False
        if connected and motion_armed:
            try:
                held = session.ctrl_c_hold()
            except Exception as current_hold_error:
                hold_error = f"{type(current_hold_error).__name__}: {current_hold_error}"
        fault = (dict(error.details) if isinstance(error, SegmentSafetyFault) else
                 {"stage": active_stage, "exception_type": type(error).__name__,
                  "driver_error": str(error)})
        fault["fault_code"] = (error.code if isinstance(error, SegmentSafetyFault)
                               else "motion_or_feedback_error")
        raise _fail(
            ("demo_fault_current_position_held" if held else
             "demo_startup_preflight_failed_no_motion" if not motion_armed else
             "demo_fault_hold_not_completed"),
            {"fault": fault, "completed_stages": completed_stages,
             "contact_reliefs": contact_reliefs, "current_position_hold": held,
             "prestart_temperatures_c": prestart_temperatures,
             "latest_temperatures_c": latest_temperatures,
             "hold_error": hold_error,
             "write_counts": dict(session.writes.counts) if connected else None},
            "fault-free supervised stage completion and current-position hold on fault",
            "원문 servo 상태와 motor ID를 확인하고, 전원을 끄기 전에 물리 상태·온도를 점검해 주세요",
        ) from error
    finally:
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
        session.close()
