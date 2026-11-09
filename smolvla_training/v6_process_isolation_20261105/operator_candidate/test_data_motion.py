import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from data_motion import Axis, ELBOW_TRAVEL, Tracking
from first_trial_core import Hold, JOINTS
from trial_runtime import DeviceIO


def controller():
    axes = [Axis(0, 2000, -100., 100.) for _ in range(5)] + [Axis(0, 1000, 0., 100.)]
    raw = dict.fromkeys(JOINTS, 1000)
    raw["gripper"] = 500
    state = [0.]*5 + [50.]
    return Tracking(axes, [60, 60, 60, 60, 50], raw, state, 0.), state


class MotionTests(unittest.TestCase):
    def test_stalled_state_does_not_reset_angular_clamp(self):
        t, state = controller()
        action = [12, 0, 0, 0, 12, 50]
        for step in range(1, 5):
            now = step/30
            row = t.propose(action, state, t.observe(state, now), now)
            goal = t.decode(row["planned_wire_raw"])
            self.assertLessEqual(goal[0]-t.previous[0], 2+1e-8)
            self.assertLessEqual(goal[4]-t.previous[4], 50/30+1e-8)
            t.sent(row["planned_wire_raw"], now)
        self.assertAlmostEqual(t.previous[0], 8)
        self.assertAlmostEqual(t.previous[4], 6.4)

    def test_15_degree_hold_sends_nothing_and_keeps_target(self):
        t, state = controller()
        state[0] = -14.
        row = t.propose([2, 0, 0, 0, 0, 50], state, t.observe(state, 1/30), 1/30)
        self.assertEqual(row["status"], "HELD")
        self.assertEqual(row["warning_joints"], ["shoulder_pan"])
        self.assertEqual(t.previous[0], 0)
        self.assertIsNone(t.last_sent)

    def test_25_degree_timer_must_be_continuous_and_is_per_axis(self):
        t, state = controller()
        state[0] = -26
        self.assertEqual(t.observe(state, .1)[0], 26)
        state[0] = 0
        t.observe(state, .55)
        state[1] = -26
        t.observe(state, .6)
        t.observe(state, 1.09)
        with self.assertRaises(Hold) as failure:
            t.observe(state, 1.1)
        self.assertEqual(failure.exception.observed["code"], "TRACKING_GAP_PERSISTENT")
        self.assertEqual(failure.exception.observed["joint"], "shoulder_lift")

    def test_elbow_undertracking_stops_after_response_window(self):
        t, state = controller()
        for step in range(1,31):
            now = step/30
            goal = 0 if step == 1 else 2.8
            row = t.propose([0, 0, goal, 0, 0, 50], state, t.observe(state, now), now)
            t.sent(row["planned_wire_raw"], now)
            state[2] = .53 if step > 4 else 0
        t.observe(state, 1.02)
        self.assertIsNone(t.response()["elbow_flex"]["ratio"])
        with self.assertRaises(Hold) as failure:
            t.observe(state, 31/30)
        self.assertEqual(failure.exception.observed["code"], "ELBOW_UNDERTRACKING")
        self.assertAlmostEqual(failure.exception.observed["ratio"], .53/2.8)

    def test_existing_active_target_and_short_excursion_do_not_count(self):
        t, state = controller()
        t.previous[2] = -2
        for step in range(1,32):
            now = step/30
            row = t.propose([0, 0, .7 if step>1 else 0, 0, 0, 50], state, t.observe(state, now), now)
            t.sent(row["planned_wire_raw"], now)
        self.assertEqual(t.response()["elbow_flex"]["status"],"NOT_ASSESSED")
        self.assertAlmostEqual(t.response()["elbow_flex"]["cumulative_sent_change"],.7)

    def test_elbow_travel_limit_applies_to_encoded_goal(self):
        t, state = controller()
        t.previous[2] = 96.
        state[2] = 96.
        row = t.propose([0, 0, 99, 0, 0, 50], state, t.observe(state, 1/30), 1/30)
        self.assertLessEqual(t.decode(row["planned_wire_raw"])[2], ELBOW_TRAVEL)
        self.assertIn("elbow_travel_or_calibration", row["clamp_reasons"]["elbow_flex"])

    def test_invalid_policy_values_and_gripper_fail(self):
        t, state = controller()
        for bad in ([0]*5, [float("nan")]+[0]*5, [0]*5+[101]):
            with self.subTest(bad=bad), self.assertRaises(Hold):
                t.propose(bad, state, [0]*6, 1/30)

    def test_encoded_sdk_route_requires_authority_and_records_wire(self):
        io = DeviceIO({}, current_limits=dict.fromkeys(JOINTS, 250), allow_goal=True, expected_torque=1)
        check = Mock()
        raw = dict.fromkeys(JOINTS, 500)
        io.robot = SimpleNamespace(bus=SimpleNamespace(sync_write=Mock()))
        io.permission = SimpleNamespace(goal_once=contextlib.nullcontext)
        with self.assertRaises(Hold):
            io.send_encoded_target([0]*6, raw, check)
        io.robot.bus.sync_write.assert_not_called()
        def sdk(*args, **kwargs):
            self.assertEqual(io.expected_wire, raw)
            io.before_wire()
            io.goal_telemetry.active.update(packet_captured=True, wire_raw=raw)
        io.robot.bus.sync_write.side_effect = sdk
        io.authorized = True
        receipt = io.send_encoded_target([0]*6, raw, check)
        io.robot.bus.sync_write.assert_called_once_with("Goal_Position", raw, normalize=False, num_retry=0)
        check.assert_called_once_with()
        self.assertEqual(receipt["wire_raw"], raw)
        self.assertEqual(receipt["motor_acknowledgement"], "UNVERIFIED")
        self.assertIsNone(io.before_wire)


if __name__ == "__main__":
    unittest.main()
