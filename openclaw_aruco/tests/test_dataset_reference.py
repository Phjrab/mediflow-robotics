from __future__ import annotations

import numpy as np
import pytest

from openclaw_aruco.dataset_reference import build_reference_library, resample_trajectory


def test_resample_trajectory_preserves_endpoints() -> None:
    source = np.asarray([[0.0, 10.0], [2.0, 14.0], [4.0, 18.0]])
    result = resample_trajectory(source, points=5)
    assert result.shape == (5, 2)
    assert result[0].tolist() == [0.0, 10.0]
    assert result[-1].tolist() == [4.0, 18.0]


def test_reference_library_rejects_motion_authority(tmp_path) -> None:
    config = {"usage": "reference_only", "motion_authorized": True, "datasets": {}}
    with pytest.raises(ValueError, match="motion-disabled"):
        build_reference_library(config, tmp_path)


def test_reference_library_requires_exact_tasks(tmp_path) -> None:
    config = {"usage": "reference_only", "motion_authorized": False, "datasets": {"A": {}}}
    with pytest.raises(ValueError, match="exactly A, B, and C"):
        build_reference_library(config, tmp_path)
