"""Regressions for the measured slow shoulder response and fixed home budget."""
import unittest

from config_runtime import load
from home_lift_fixture import LoadedLiftArm
from start_position_rules import home_targets
from startup_simulation import SimulatedArm
import direct_live as base
import live_start


class HomeSettleTests(unittest.TestCase):
    def prepare(self, late_progress=True, arm_type=LoadedLiftArm):
        events = []
        io = arm_type(load()['home']['state'], {}, events.append, 'all_off',
                      late_progress=late_progress)
        return io, events

    def test_late_progress_uses_saved_time_at_same_goal_then_confirms_home(self):
        io, events = self.prepare()
        result = live_start.prepare(io, io.home, events.append)
        tries = [e for e in events if e['event']=='startup_home_attempt']
        self.assertEqual([e['command_ticks']['shoulder_lift'] for e in tries], [842, 834, 826])
        extension = next(e for e in events if e['event']=='startup_home_settle_extension')
        self.assertGreaterEqual(extension['recent_progress_ticks'], 1)
        self.assertEqual(extension['active_target_ticks'], 826)
        rows = [e for e in events if e['event']=='startup_home_result']
        self.assertLess(sum(e['settle_elapsed_s'] for e in rows), 9.)
        self.assertEqual(result['position_check']['joints']['shoulder_lift']['residual_ticks'], 6)
        confirmed = next(e for e in events if e['event']=='startup_home_converged')
        self.assertGreaterEqual(confirmed['stable_seconds']+1e-9, .5)

    def test_plateau_remains_hold_even_with_additional_observation(self):
        io, events = self.prepare(late_progress=False)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            live_start.prepare(io, io.home, events.append)
        attempts = [e for e in events if e['event']=='startup_home_result']
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[-1]['joints']['shoulder_lift']['residual_ticks'], 8)
        self.assertLessEqual(sum(e['settle_elapsed_s'] for e in attempts), 9.051)
        self.assertFalse(any(e['event']=='startup_ready' for e in events))

    def test_target_feedback_mismatch_does_not_advance_trim(self):
        class MissingTargetReadback(LoadedLiftArm):
            def start_snapshot(self):
                sample = super().start_snapshot()
                if self.packet_count:
                    sample['active_targets']['shoulder_lift'] = 858
                return sample

        io, events = self.prepare(arm_type=MissingTargetReadback)
        with self.assertRaisesRegex(base.Stop, '목표 응답 불일치'):
            live_start.prepare(io, io.home, events.append)
        self.assertFalse(any(e['event']=='startup_home_trim' for e in events))

    def test_extended_settle_checks_health_and_does_not_send_more_goals(self):
        class StopsWithTemperature(LoadedLiftArm):
            def start_snapshot(self):
                sample = super().start_snapshot()
                if self.target['shoulder_lift']==826 and self.clock()-self.last_lift_send_at>3.45:
                    sample['temperature_protection']['shoulder_lift']['hold']=True
                return sample

        io, events = self.prepare(arm_type=StopsWithTemperature)
        sent_at_extension = []
        def record(event):
            events.append(event)
            if event['event']=='startup_home_settle_extension':
                sent_at_extension.append(io.packet_count)
        with self.assertRaisesRegex(base.Stop, '상태코드/온도'):
            live_start.prepare(io, io.home, record)
        self.assertEqual(sent_at_extension, [io.packet_count])

    def test_other_axis_does_not_use_lift_wait_budget(self):
        class FixedWrist(SimulatedArm):
            def feedback(self):
                sample = super().feedback()
                sample['present_raw']['wrist_roll'] = home_targets()['wrist_roll']+7
                sample['state'] = self.normalize(sample['present_raw'])
                return sample

        events = []
        io = FixedWrist(load()['home']['state'], {}, events.append, 'camera_dry')
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            live_start.prepare(io, io.home, events.append)
        self.assertFalse(any(e.get('settle_early') or e['event']=='startup_home_settle_extension'
                             for e in events))
