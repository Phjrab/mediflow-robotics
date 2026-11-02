from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import SorterError
from .models import BasketColor, BottleSize


TASK_REQUIREMENTS = {
    "offline_mock": (),
    "robot_status": ("robot_identity",),
    "camera_status": ("camera_source",),
    "robot_calibration": ("robot_identity",),
    "camera_calibration": ("camera_source",),
    # Teaching acquires the fixed path, so it must not require a path that does
    # not exist yet. It still needs a verified joint model and a connection
    # lifecycle that cannot change torque unexpectedly.
    "supervised_teaching": (
        "robot_identity", "joint", "torque_lifecycle", "robot.live_enabled",
    ),
    # A taught, fixed-location demo does not depend on camera/table geometry.
    # It does require the exact taught path and the TCP used for clearance checks.
    "fixed_demo": (
        "robot_identity", "joint", "tcp", "motion_paths",
        "torque_lifecycle", "robot.live_enabled",
    ),
    # Compatibility name retained for callers that used the earlier task list.
    "supervised_motion": (
        "robot_identity", "joint", "tcp", "motion_paths",
        "torque_lifecycle", "robot.live_enabled",
    ),
    "full_sort": (
        "robot_identity", "camera_source", "joint", "camera_intrinsics",
        "camera_to_table", "table_to_robot_base", "tcp", "basket_openings",
        "motion_paths", "torque_lifecycle", "robot.live_enabled",
    ),
}


@dataclass(frozen=True)
class SystemConfig:
    path: Path
    raw: dict[str, Any]

    @property
    def config_id(self) -> str:
        return self.raw["config_id"]

    @property
    def max_scene_age_s(self) -> float:
        return float(self.raw["vision"]["max_scene_age_s"])

    @property
    def basket_markers(self) -> dict[BasketColor, int]:
        return {BasketColor(color): int(item["marker_id"])
                for color, item in self.raw["baskets"].items()}

    @property
    def default_destinations(self) -> dict[BottleSize, BasketColor]:
        return {BottleSize(size): BasketColor(color)
                for size, color in self.raw.get("default_destinations", {}).items()}

    def readiness(self, task: str = "full_sort") -> dict[str, Any]:
        if task not in TASK_REQUIREMENTS:
            raise ValueError(f"unsupported_readiness_task:{task}")
        robot = self.raw["robot"]
        vision = self.raw["vision"]
        calibration = self.raw["calibration"]
        missing = []
        for requirement in TASK_REQUIREMENTS[task]:
            if requirement == "robot_identity":
                if (robot.get("follower_port") not in {"/dev/ttyACM0", "/dev/ttyACM1"} or
                        not isinstance(robot.get("follower_serial"), str) or
                        not robot["follower_serial"].strip()):
                    missing.append("robot.identity")
            elif requirement == "camera_source":
                source = vision.get("camera_source")
                if not isinstance(source, str) or not source or source.startswith("VERIFY_"):
                    missing.append("vision.camera_source")
            elif requirement == "robot.live_enabled":
                if robot.get("live_enabled") is not True:
                    missing.append(requirement)
            else:
                item = calibration.get(requirement)
                if not isinstance(item, dict) or item.get("status") != "verified":
                    missing.append(requirement)
        result = {
            "task": task,
            "config_id": self.config_id,
            "ready": not missing,
            "missing_or_unverified": missing,
            "default_mode": "no_hardware",
        }
        if task == "full_sort":
            result["live_ready"] = result["ready"]
        return result

    def readiness_by_task(self) -> dict[str, dict[str, Any]]:
        return {task: self.readiness(task) for task in TASK_REQUIREMENTS}


def load_config(path: Path) -> SystemConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SorterError("config_load_failed", str(error), "유효한 JSON 설정",
                          "설정 파일 경로와 JSON 형식을 확인해 주세요") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise SorterError("unsupported_config_schema", raw.get("schema_version") if isinstance(raw, dict) else None,
                          "schema_version=1", "현재 예제 설정에서 새 환경 설정을 만들어 주세요")
    required = {"config_id", "units", "robot", "vision", "markers", "baskets", "bottle_types", "calibration"}
    missing = sorted(required - set(raw))
    if missing:
        raise SorterError("config_fields_missing", missing, ", ".join(sorted(required)),
                          "누락된 설정을 추가해 주세요")
    if raw["units"] != {"length": "mm", "angle": "degree"}:
        raise SorterError("unsupported_internal_units", raw["units"], "length=mm, angle=degree",
                          "입출력 경계에서만 단위를 변환해 주세요")
    robot = raw["robot"]
    if (not isinstance(robot, dict) or
            robot.get("follower_port") not in {"/dev/ttyACM0", "/dev/ttyACM1"} or
            not isinstance(robot.get("follower_serial"), str) or
            not robot["follower_serial"].strip()):
        raise SorterError("robot_identity_config_invalid", robot,
                          "고정 tty 포트와 follower USB serial",
                          "Jetson에서 follower 실물 식별자를 다시 확인해 주세요")
    markers = raw["markers"]
    printed_size = markers.get("printed_marker_size_mm") if isinstance(markers, dict) else None
    if (not isinstance(markers, dict) or
            markers.get("dictionary") != "DICT_4X4_50" or
            not isinstance(printed_size, (int, float)) or isinstance(printed_size, bool) or
            printed_size <= 0):
        raise SorterError("marker_print_config_invalid", markers,
                          "DICT_4X4_50의 양수 marker size_mm",
                          "인쇄한 검은 사각형의 실측 크기를 설정해 주세요")
    reference = markers.get("reference")
    if (not isinstance(reference, dict) or set(reference) != {"0", "1", "2", "3"} or
            any(not isinstance(item, dict) or item.get("size_mm") != printed_size
                for item in reference.values())):
        raise SorterError("reference_marker_size_mismatch", reference,
                          f"ID0–3 size_mm={printed_size}",
                          "작업대 마커 실측 크기와 설정을 일치시켜 주세요")
    baskets = raw["baskets"]
    expected_colors = {"red", "green", "blue"}
    if not isinstance(baskets, dict) or set(baskets) != expected_colors:
        raise SorterError("basket_mapping_invalid", baskets, "red/green/blue 각각 하나",
                          "새 환경의 실제 ID↔색 부착 상태를 확인해 주세요")
    marker_ids = []
    for color in sorted(expected_colors):
        item = baskets[color]
        marker_id = item.get("marker_id") if isinstance(item, dict) else None
        if not isinstance(marker_id, int) or isinstance(marker_id, bool) or marker_id < 0:
            raise SorterError("basket_marker_invalid", {color: item}, "0 이상 정수 marker ID",
                              "실제 부착 ID를 확인해 설정해 주세요")
        marker_ids.append(marker_id)
    if len(set(marker_ids)) != 3:
        raise SorterError("duplicate_basket_marker", marker_ids, "서로 다른 marker ID",
                          "바구니 marker 설정을 수정해 주세요")
    if set(raw["bottle_types"]) != {"large", "medium", "small"}:
        raise SorterError("bottle_types_invalid", sorted(raw["bottle_types"]),
                          "large/medium/small", "A/B/C 실물 정의를 확인해 주세요")
    return SystemConfig(path, raw)
