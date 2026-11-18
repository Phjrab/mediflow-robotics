"""Home start and fixed-target recovery, using an in-memory servo fixture."""
import math
import json
import unittest

from config_runtime import load, command_limits
from start_position_rules import home_targets
from startup_simulation import SimulatedArm
import direct_live as base
import live_start


class BiasedHomeArm(SimulatedArm):
    """First-order servo with a persistent lift equilibrium error, not gain edits."""
    def __init__(self, bias=14, *, frozen=False, moving=False, fault=None):
        self.events = []
        self.sent = []
        self.attempt = 0
        self.bias = bias
        self.frozen = frozen
        self.moving = moving
        self.fault = fault
        c = load()
        super().__init__(c['home']['state'], {}, self.record, 'camera_dry')
        self.present = {j: float(v) for j, v in home_targets(c).items()}
        self.present['shoulder_lift'] += bias
        self.target = home_targets(c)
        self.initial = dict(self.present)

    def record(self, row):
        self.events.append(row)
        if row.get('event') == 'startup_home_attempt':
            self.attempt = row['attempt']
        if row.get('event') == 'startup_ready':
            self.policy_active = True

    def feedback(self):
        commanded = dict(self.target)
        self.target['shoulder_lift'] += self.bias
        try:
            sample = super().feedback()
        finally:
            self.target = commanded
        if self.frozen:
            self.present = dict(self.initial)
        if self.moving:
            # Remains well outside home, with >2 ticks of travel per 0.5s.
            self.present['shoulder_lift'] = home_targets()['shoulder_lift'] + 30 + 8 * math.sin(self.clock() * 8)
        raw = {j: round(v) for j, v in self.present.items()}
        sample.update(present_raw=raw, state=self.normalize(raw), active_targets=commanded)
        if self.fault == 'temperature' and self.packet_count >= 5:
            sample['temperatures']['shoulder_lift'] = 55
            sample['temperature_protection']['shoulder_lift']['hold'] = True
        if self.fault == 'status' and self.packet_count >= 5:
            sample['statuses']['shoulder_lift'] = 32
        return sample

    def checked(self):
        if self.packet_count >= 5:
            if self.fault == 'communication':
                raise OSError('injected communication failure')
            if self.fault == 'ctrl_c':
                raise KeyboardInterrupt
        return super().checked()

    start_snapshot = checked

    def send(self, raw):
        self.sent.append(dict(time=self.clock(), attempt=self.attempt, raw=dict(raw)))
        super().send(raw)


class HomeRetryTests(unittest.TestCase):
    def test_recorded_lift_positions_start_without_home_motion(self):
        for lift in (850, 856):
            with self.subTest(lift=lift):
                io = BiasedHomeArm(lift-842)
                result = live_start.prepare(io, io.home, io.record)
                self.assertEqual(io.sent, [])
                self.assertEqual(result['position_check']['joints']['shoulder_lift']['present_ticks'], lift)
                self.assertGreaterEqual(io.clock(), .5)

    def test_beyond_capped_window_retries_same_goal_and_holds(self):
        io = BiasedHomeArm(24, frozen=True)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패') as error:
            live_start.prepare(io, io.home, io.record)
        detail = json.loads(str(error.exception).split(': ',1)[1])
        self.assertEqual(detail['attempts'], 3)
        self.assertEqual(detail['joints']['shoulder_lift']['present_ticks'], 866)
        self.assertEqual([r['raw'] for r in io.sent if r['attempt'] >= 2],
                         [home_targets(), home_targets()])
        self.assertEqual([e['command_ticks'] for e in io.events if e['event']=='startup_home_attempt'],
                         [home_targets()] * 3)
        for row in io.sent:
            for joint, ticks in row['raw'].items():
                lo, hi = command_limits()[joint]
                self.assertLessEqual(lo, ticks)
                self.assertLessEqual(ticks, hi)

    def test_health_faults_stop_home_motion(self):
        for fault, error_type in (('temperature', base.Stop), ('status', base.Stop),
                                  ('communication', OSError), ('ctrl_c', KeyboardInterrupt)):
            with self.subTest(fault=fault):
                io = BiasedHomeArm(24, fault=fault)
                with self.assertRaises(error_type):
                    live_start.prepare(io, io.home, io.record)
                self.assertEqual(io.packet_count, 5)

    def test_stale_feedback_cannot_start_at_accepted_pose(self):
        class GappedArm(BiasedHomeArm):
            def checked(self):
                self.pause(.4)
                return super().checked()
            start_snapshot = checked

        io = GappedArm(14, frozen=True)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            live_start.prepare(io, io.home, io.record)
        self.assertFalse(any(r['event']=='startup_ready' for r in io.events))
