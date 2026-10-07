#!/usr/bin/env python3
"""Fit dry-run T_B_W from manually reviewed World/Robot Base point pairs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def validate_pairs(document: dict) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if document.get("unit") != "mm":
        raise ValueError("teach point unit must be mm")
    if document.get("convention") != "T_B_W transforms a point from World into Robot Base":
        raise ValueError("unexpected transform convention")
    records = document.get("pairs")
    if not isinstance(records, list) or not 4 <= len(records) <= 8:
        raise ValueError("4 to 8 teach point pairs are required")
    names = []
    world = []
    base = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"pair {index} must be an object")
        name = record.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("teach point names must be non-empty and unique")
        names.append(name)
        for key, target in (("world_mm", world), ("robot_base_mm", base)):
            value = record.get(key)
            if (
                not isinstance(value, list)
                or len(value) != 3
                or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
                or not np.isfinite(value).all()
            ):
                raise ValueError(f"{name}.{key} must contain three finite numbers")
            target.append(value)
    world_array = np.asarray(world, dtype=np.float64)
    base_array = np.asarray(base, dtype=np.float64)
    if np.linalg.matrix_rank(world_array - world_array.mean(axis=0)) < 2:
        raise ValueError("World teach points must span at least two dimensions")
    if np.linalg.matrix_rank(base_array - base_array.mean(axis=0)) < 2:
        raise ValueError("Robot Base teach points must span at least two dimensions")
    return world_array, base_array, names


def fit_rigid_transform(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target must have matching Nx3 shapes")
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    predicted = (rotation @ source.T).T + translation
    residuals = np.linalg.norm(predicted - target, axis=1)
    return transform, residuals


def similarity_scale_diagnostic(source: np.ndarray, target: np.ndarray) -> float:
    source_centered = source - source.mean(axis=0)
    target_centered = target - target.mean(axis=0)
    denominator = float(np.square(source_centered).sum())
    if denominator <= 0:
        raise ValueError("source point spread is zero")
    singular_values = np.linalg.svd(source_centered.T @ target_centered, compute_uv=False)
    return float(singular_values.sum() / denominator)


def build_result(document: dict) -> dict:
    world, base, names = validate_pairs(document)
    transform, residuals = fit_rigid_transform(world, base)
    return {
        "schema_version": 1,
        "status": "FIT_ONLY_REQUIRES_DRY_RUN_VALIDATION",
        "unit": "mm",
        "convention": "T_B_W transforms a point from World into Robot Base",
        "T_B_W": transform.tolist(),
        "rotation_determinant": float(np.linalg.det(transform[:3, :3])),
        "rmse_mm": float(np.sqrt(np.mean(np.square(residuals)))),
        "max_error_mm": float(residuals.max()),
        "similarity_scale_diagnostic_not_applied": similarity_scale_diagnostic(world, base),
        "points": [
            {"name": name, "residual_mm": float(error)}
            for name, error in zip(names, residuals)
        ],
        "source_pairs": len(names),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "robot_enabled": False,
        "motion_authorized": False,
        "note": "Rigid fit only. Validate FK, axes, units, limits and independent dry-run points before motion.",
    }


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise ValueError("output already exists; calibration results are never overwritten")
    if not args.output.parent.is_dir():
        raise ValueError("output parent does not exist")
    document = json.loads(args.pairs.read_text(encoding="utf-8"))
    result = build_result(document)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"FIT points={result['source_pairs']} rmse_mm={result['rmse_mm']:.3f} "
        f"max_error_mm={result['max_error_mm']:.3f} robot_enabled=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
