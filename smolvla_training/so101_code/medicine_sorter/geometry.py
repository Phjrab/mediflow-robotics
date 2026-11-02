from __future__ import annotations

import math
from dataclasses import dataclass


def _finite(values) -> bool:
    return all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values)


@dataclass(frozen=True)
class RigidTransform:
    """T_A_B maps a point expressed in frame B into frame A."""

    target_frame: str
    source_frame: str
    rotation: tuple[tuple[float, float, float], ...]
    translation_mm: tuple[float, float, float]

    def __post_init__(self) -> None:
        if len(self.rotation) != 3 or any(len(row) != 3 for row in self.rotation):
            raise ValueError("rotation_must_be_3x3")
        flat = [value for row in self.rotation for value in row]
        if not _finite(flat) or not _finite(self.translation_mm):
            raise ValueError("transform_must_be_finite")
        for i in range(3):
            for j in range(3):
                dot = sum(self.rotation[k][i] * self.rotation[k][j] for k in range(3))
                if abs(dot - (1.0 if i == j else 0.0)) > 1e-6:
                    raise ValueError("rotation_not_orthonormal")

    @property
    def name(self) -> str:
        return f"T_{self.target_frame}_{self.source_frame}"

    def apply_mm(self, point_source_mm: tuple[float, float, float]) -> tuple[float, float, float]:
        if not _finite(point_source_mm):
            raise ValueError("point_must_be_finite_mm")
        return tuple(sum(self.rotation[i][j] * point_source_mm[j] for j in range(3))
                     + self.translation_mm[i] for i in range(3))

    def inverse(self) -> "RigidTransform":
        rotation = tuple(tuple(self.rotation[j][i] for j in range(3)) for i in range(3))
        translation = tuple(-sum(rotation[i][j] * self.translation_mm[j] for j in range(3))
                            for i in range(3))
        return RigidTransform(self.source_frame, self.target_frame, rotation, translation)

    def then(self, outer: "RigidTransform") -> "RigidTransform":
        if self.target_frame != outer.source_frame:
            raise ValueError("transform_frame_mismatch")
        rotation = tuple(tuple(sum(outer.rotation[i][k] * self.rotation[k][j]
                                   for k in range(3)) for j in range(3)) for i in range(3))
        translation = outer.apply_mm(self.translation_mm)
        return RigidTransform(outer.target_frame, self.source_frame, rotation, translation)


def mm_to_m(value_mm: float) -> float:
    if not _finite((value_mm,)):
        raise ValueError("length_must_be_finite")
    return value_mm / 1000.0


def m_to_mm(value_m: float) -> float:
    if not _finite((value_m,)):
        raise ValueError("length_must_be_finite")
    return value_m * 1000.0
