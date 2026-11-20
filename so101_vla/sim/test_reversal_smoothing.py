import unittest

from legacy_motion_options import load_options, reversal_offsets, smooth_arm_packet
from direct_live import JOINTS


class ReversalSmoothingTests(unittest.TestCase):
    def test_default_straight_segments_and_buffer_boundaries(self):
        self.assertIsNone(load_options(False, None, .4))
        slots = {k: [0., float(k), -float(k), 0., 0., 0.] for k in range(4)}
        for phase in (0., .5, 1., 1.75, 3.):
            self.assertEqual(reversal_offsets(slots, phase), [0., 0.])

    def test_only_known_extrema_are_reduced_with_the_same_interpolation(self):
        slots = {k: [0., pos, -pos, 0., 0., 0.] for k, pos in enumerate([0., 20., 18., 17.])}
        self.assertEqual(reversal_offsets(slots, 1.), [-1., 1.])
        self.assertEqual(reversal_offsets(slots, 1.5), [-.5, .5])
        self.assertEqual(reversal_offsets(slots, 2.), [0., 0.])

    def test_clipping_gripper_and_other_joints_are_preserved(self):
        original = dict.fromkeys(JOINTS, 100)
        bounds = dict.fromkeys(JOINTS, (0, 101))
        plan = dict(wire_raw=original.copy(), wire_normalized=list(range(6)), clipped_joints=['elbow_flex'])
        delta = smooth_arm_packet(plan, [1., -1.], bounds, lambda raw: list(raw.values()))
        self.assertEqual(delta, {'shoulder_lift': 1, 'elbow_flex': 0})
        for axis in (0, 3, 4, 5):
            self.assertEqual(plan['wire_raw'][JOINTS[axis]], 100)
            self.assertEqual(plan['wire_normalized'][axis], axis)
        self.assertEqual(plan['wire_raw']['elbow_flex'], 100)
