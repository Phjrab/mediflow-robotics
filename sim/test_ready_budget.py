"""READY budget and fail-closed priming regressions, without any devices."""
import contextlib
import io as text_io
import unittest

import direct_live as base
from config_runtime import load
from startup_simulation import SimulatedArm
from stream_live import stream


class ReadyBudgetTests(unittest.TestCase):
    def make_arm(self):
        home = list(load()['home']['state'])
        arm = SimulatedArm(home, {}, lambda _: None, 'camera_dry')
        arm.policy_active = True
        return arm, home

    def run_stream(self, arm, policy, duration=.4, monitor=None):
        rows = []
        with contextlib.redirect_stdout(text_io.StringIO()):
            result = stream(arm, policy, rows.append, duration, .33, monitor)
        return result, rows

    def test_first_inference_is_outside_budget_and_first_action_is_zero(self):
        arm, home = self.make_arm()
        requests = []

        def policy(frames, state, origin, delay):
            requests.append((origin, delay))
            actions = [home[:-1]+[home[-1]+index*.1] for index in range(50)]
            return actions, 5.8

        result, rows = self.run_stream(arm, policy)
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertGreater(result['steps'], 0)
        self.assertGreaterEqual(result['ready']['monotonic_s'], 5.8)
        self.assertGreaterEqual(result['elapsed_s'], .4)
        self.assertLess(result['elapsed_s'], .5)
        self.assertGreaterEqual(arm.clock(), 6.2)
        first = next(r for r in rows if r['event']=='goal_transmitted')
        self.assertEqual(first['model_phase'], 0)
        self.assertLess(first['time_s'], .05)
        self.assertEqual(requests[0], (0, 0))

    def test_health_fault_while_priming_blocks_all_goals(self):
        arm, home = self.make_arm()
        checked = arm.checked

        def fault():
            if arm.clock() >= .1:
                raise base.Stop('fixture temperature fault')
            return checked()

        arm.checked = fault
        result, _ = self.run_stream(arm, lambda *args: ([home]*50, 1.5))
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('fixture temperature fault', result['reason'])
        self.assertEqual(result['steps'], 0)
        self.assertEqual(arm.packet_count, 0)
        self.assertEqual(arm.start_writes, [])
        self.assertIsNone(result['ready'])

    def test_ctrl_c_while_priming_blocks_all_goals(self):
        arm, home = self.make_arm()
        checked = arm.checked

        def interrupt():
            if arm.clock() >= .1:
                raise KeyboardInterrupt
            return checked()

        arm.checked = interrupt
        result, _ = self.run_stream(arm, lambda *args: ([home]*50, 1.5))
        self.assertEqual((result['status'], result['reason']), ('STOPPED', 'Ctrl+C'))
        self.assertEqual(arm.packet_count, 0)
        self.assertEqual(arm.start_writes, [])
        self.assertIsNone(result['ready'])

    def test_no_camera_for_five_seconds_never_predicts_or_writes(self):
        arm, _ = self.make_arm()
        arm.fresh_frames = lambda: None

        def forbidden(*args):
            self.fail('camera-unavailable path must not call policy')

        result, _ = self.run_stream(arm, forbidden)
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('5초', result['reason'])
        self.assertGreaterEqual(arm.clock(), 5)
        self.assertLess(arm.clock(), 5.1)
        self.assertEqual(arm.packet_count, 0)
        self.assertIsNone(result['ready'])

    def test_inference_watchdog_remains_active_before_ready(self):
        arm, home = self.make_arm()
        result, _ = self.run_stream(arm, lambda *args: ([home]*50, 20))
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('추론 응답 시간 초과', result['reason'])
        self.assertLess(arm.clock(), 9.1)
        self.assertEqual(arm.packet_count, 0)
        self.assertIsNone(result['ready'])


if __name__ == '__main__':
    unittest.main()
