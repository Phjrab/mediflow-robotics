from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .config import SystemConfig
from .errors import SorterError
from .models import BasketColor, BasketObservation, BottleCandidate, BottleSize, SceneObservation, SortCommand


def _xyz(value, field: str):
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 3 or
            any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item)
                for item in value)):
        raise SorterError("invalid_scene_coordinate", {field: value}, "유한한 mm XYZ 3개",
                          "관측 생성기를 확인해 주세요")
    return tuple(float(item) for item in value)


def load_scene(path: Path, config: SystemConfig, *, recorded: bool = False,
               now_s: float | None = None) -> SceneObservation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SorterError("scene_load_failed", str(error), "유효한 scene JSON",
                          "저장 장면 경로와 형식을 확인해 주세요") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise SorterError("unsupported_scene_schema", raw.get("schema_version") if isinstance(raw, dict) else None,
                          "schema_version=1", "현재 scene 형식으로 다시 저장해 주세요")
    if raw.get("config_id") != config.config_id:
        raise SorterError("scene_config_mismatch", raw.get("config_id"), config.config_id,
                          "현재 설정으로 장면을 다시 관측해 주세요")
    observed = raw.get("observed_at_s")
    if not isinstance(observed, (int, float)) or isinstance(observed, bool) or not math.isfinite(observed):
        raise SorterError("invalid_scene_timestamp", observed, "유한한 epoch seconds",
                          "관측 시각을 기록해 주세요")
    now = float(observed) if recorded else (time.time() if now_s is None else now_s)
    age = now - float(observed)
    if age < 0 or age > config.max_scene_age_s:
        raise SorterError("stale_scene", round(age, 3), f"0..{config.max_scene_age_s}s",
                          "새 장면을 관측해 주세요")
    if raw.get("table_frame_valid") is not True:
        raise SorterError("table_frame_invalid", raw.get("table_frame_valid"), "ID0–3 기반 유효 작업대 frame",
                          "새 환경 marker 배치와 변환을 검증해 주세요")
    if raw.get("rgb_depth_aligned") is not True:
        raise SorterError("rgb_depth_mismatch", raw.get("rgb_depth_aligned"), "정합된 RGB/depth",
                          "RGB-D 정합과 timestamp를 다시 확인해 주세요")

    bottles = []
    for item in raw.get("bottles", []):
        try:
            size = None if item.get("size") is None else BottleSize(item["size"])
            pixel = item["pixel_center"]
            if len(pixel) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pixel):
                raise ValueError
            bottles.append(BottleCandidate(
                str(item["observation_id"]), str(item["class_name"]), size,
                float(item["detection_confidence"]),
                None if item.get("size_confidence") is None else float(item["size_confidence"]),
                str(item.get("classification_basis", "unknown")), tuple(map(float, pixel)),
                float(observed), _xyz(item.get("table_position_mm"), "bottle.table_position_mm"),
                item.get("graspable") is True, item.get("reason")))
        except (KeyError, TypeError, ValueError) as error:
            raise SorterError("invalid_bottle_observation", item, "완전한 bottle candidate",
                              "비전 출력 schema를 확인해 주세요") from error
    baskets = []
    for item in raw.get("baskets", []):
        try:
            baskets.append(BasketObservation(
                int(item["marker_id"]), BasketColor(item["color"]), float(observed),
                _xyz(item.get("opening_table_mm"), "basket.opening_table_mm"),
                item.get("verified") is True, item.get("reason")))
        except (KeyError, TypeError, ValueError) as error:
            raise SorterError("invalid_basket_observation", item, "완전한 basket observation",
                              "marker mapping 출력을 확인해 주세요") from error
    return SceneObservation(str(raw.get("frame_id", "unknown")), float(observed), config.config_id,
                            True, True, tuple(bottles), tuple(baskets))


def select_target(scene: SceneObservation, command: SortCommand) -> BottleCandidate:
    matching = [item for item in scene.bottles if item.size == command.size and item.graspable]
    if not matching:
        uncertain = [item.observation_id for item in scene.bottles if item.size is None]
        raise SorterError("target_not_found", {"size": command.size.value, "uncertain": uncertain},
                          "신뢰 가능한 집기 후보 하나 이상", "대상 약통을 다시 관측해 주세요")
    if len(matching) == 1:
        return matching[0]
    if any(item.table_position_mm is None for item in matching):
        raise SorterError("multiple_targets_ambiguous", [item.observation_id for item in matching],
                          "위치가 계산된 유일 후보", "후보 간격을 늘리거나 하나만 남겨 주세요")
    ranked = sorted(matching, key=lambda item: (math.hypot(item.table_position_mm[0], item.table_position_mm[1]),
                                                item.observation_id))
    first_distance = math.hypot(ranked[0].table_position_mm[0], ranked[0].table_position_mm[1])
    second_distance = math.hypot(ranked[1].table_position_mm[0], ranked[1].table_position_mm[1])
    if abs(first_distance - second_distance) < 1.0:
        raise SorterError("multiple_targets_tied", [item.observation_id for item in ranked[:2]],
                          "table origin 거리 차이 1mm 이상", "대상을 하나만 남기거나 명시 선택 기능을 사용해 주세요")
    return ranked[0]


def select_basket(scene: SceneObservation, command: SortCommand,
                  config: SystemConfig) -> BasketObservation:
    expected_id = config.basket_markers[command.destination_color]
    matching = [item for item in scene.baskets
                if item.color == command.destination_color and item.marker_id == expected_id]
    if len(matching) != 1 or not matching[0].verified:
        raise SorterError("destination_not_verified", {
            "color": command.destination_color.value,
            "expected_marker_id": expected_id,
            "matches": [item.marker_id for item in matching],
        }, "등록된 marker와 opening이 검증된 바구니 하나",
            "바구니 ID·색·고정 위치를 다시 확인해 주세요")
    return matching[0]
