from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class BottleSize(str, Enum):
    LARGE = "large"
    MEDIUM = "medium"
    SMALL = "small"


class BasketColor(str, Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


@dataclass(frozen=True)
class SortCommand:
    action: str
    object_type: str
    size: BottleSize
    destination_color: BasketColor
    quantity: int = 1
    source_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["size"] = self.size.value
        result["destination_color"] = self.destination_color.value
        return result


@dataclass(frozen=True)
class BottleCandidate:
    observation_id: str
    class_name: str
    size: BottleSize | None
    detection_confidence: float
    size_confidence: float | None
    classification_basis: str
    pixel_center: tuple[float, float]
    observed_at_s: float
    table_position_mm: tuple[float, float, float] | None
    graspable: bool
    reason: str | None = None


@dataclass(frozen=True)
class BasketObservation:
    marker_id: int
    color: BasketColor
    observed_at_s: float
    opening_table_mm: tuple[float, float, float] | None
    verified: bool
    reason: str | None = None


@dataclass(frozen=True)
class SceneObservation:
    frame_id: str
    observed_at_s: float
    config_id: str
    table_frame_valid: bool
    rgb_depth_aligned: bool
    bottles: tuple[BottleCandidate, ...]
    baskets: tuple[BasketObservation, ...]


@dataclass(frozen=True)
class TaskPlan:
    target_observation_id: str
    destination_marker_id: int
    stages: tuple[str, ...]
    mode: str
    physical_motion_authorized: bool


@dataclass(frozen=True)
class TaskResult:
    status: str
    command: SortCommand
    target_observation_id: str
    destination_marker_id: int
    completed_stages: tuple[str, ...]
    result_verification: str
    physical_motion: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command.as_dict(),
            "target_observation_id": self.target_observation_id,
            "destination_marker_id": self.destination_marker_id,
            "completed_stages": list(self.completed_stages),
            "result_verification": self.result_verification,
            "physical_motion": self.physical_motion,
        }
