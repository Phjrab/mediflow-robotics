from __future__ import annotations

import numpy as np

from openclaw_aruco.recorded_replay_plan import interpolate_limited, preserve_source_actions


def test_interpolate_limited_respects_body_and_gripper_limits() -> None:
    start = np.zeros(3)
    target = np.asarray([1.2, -0.6, 3.1])
    points = interpolate_limited(start, target, body_step=0.5, gripper_step=1.0)
    path = np.vstack([start, points])
    steps = np.abs(np.diff(path, axis=0))
    assert np.all(steps[:, :2] <= 0.5 + 1e-9)
    assert np.all(steps[:, 2] <= 1.0 + 1e-9)
    assert np.allclose(path[-1], target)


def test_degree_actions_outside_old_bounds_are_flagged_not_clipped() -> None:
    source = np.asarray([[0.0, 102.75, 45.0], [-3.0, 99.0, 46.0]])
    retained, exceedances = preserve_source_actions(source)
    assert exceedances == 1
    assert retained[0, 1] == 102.75
    assert np.array_equal(retained, source)
