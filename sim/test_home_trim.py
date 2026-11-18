"""Bounded home trim regressions. All servo and time operations are in memory."""
import math
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config_runtime
from config_runtime import load, command_limits
from start_position_rules import home_targets, position_check
from startup_simulation import SimulatedArm
import direct_live as base
import live_start
from stream_live import start_policy_session


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


class HomeTrimTests(unittest.TestCase):
    def prepare(self, io):
        return live_start.prepare(io, io.home, io.record)

    def assert_bounds_and_scope(self, io):
        canonical = home_targets()
        limits = command_limits()
        for row in io.sent:
            self.assertTrue(all(limits[j][0] <= raw <= limits[j][1] for j, raw in row['raw'].items()))
            self.assertTrue(all(row['raw'][j] == canonical[j] for j in canonical if j != 'shoulder_lift'))
        self.assertTrue(all(reg in ('Goal_Position', 'Torque_Enable') for reg, _ in io.start_writes))
        trim = getattr(io, 'home_command_trim', {})
        self.assertLessEqual(abs(trim.get('shoulder_lift', 0)), 16)
        self.assertTrue(all(value == 0 for joint, value in trim.items() if joint != 'shoulder_lift'))

    def test_recorded_fourteen_and_fifteen_tick_bias_reach_true_home(self):
        for bias in (14, 15):
            with self.subTest(bias=bias):
                io = BiasedHomeArm(bias)
                self.prepare(io)
                final = position_check(io.checked()['present_raw'])
                self.assertTrue(final['home_converged'])
                self.assertEqual(final['joints']['shoulder_lift']['target_ticks'], 842)
                self.assertLessEqual(abs(final['joints']['shoulder_lift']['residual_ticks']), 6)
                self.assertLess(getattr(io, 'home_command_trim', {}).get('shoulder_lift', 0), 0)
                self.assertTrue(any(row['event'] == 'startup_home_trim' for row in io.events))
                self.assert_bounds_and_scope(io)

    def test_correct_home_uses_no_trim_and_no_goal_packet(self):
        io = BiasedHomeArm(0)
        self.prepare(io)
        self.assertEqual(io.sent, [])
        self.assertFalse(any(row['event'] == 'startup_home_trim' for row in io.events))
        self.assertTrue(all(value == 0 for value in getattr(io, 'home_command_trim', {}).values()))

    def test_negative_static_residual_trims_in_positive_direction(self):
        io = BiasedHomeArm(-14)
        self.prepare(io)
        self.assertGreater(io.home_command_trim['shoulder_lift'], 0)
        self.assertTrue(position_check(io.checked()['present_raw'])['home_converged'])
        self.assert_bounds_and_scope(io)

    def test_twenty_five_tick_bias_cannot_escape_six_tick_rule_at_trim_cap(self):
        io = BiasedHomeArm(25)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패') as error:
            self.prepare(io)
        detail = json.loads(str(error.exception).split(': ', 1)[1])
        self.assertEqual(io.home_command_trim['shoulder_lift'], -16)
        self.assertGreater(detail['joints']['shoulder_lift']['residual_ticks'], 6)
        self.assertEqual(sum(row['event'] == 'startup_home_attempt' for row in io.events), 3)
        self.assert_bounds_and_scope(io)

    def test_disappearing_bias_does_not_create_false_home_success(self):
        class DisappearingBiasArm(BiasedHomeArm):
            def __init__(self):
                self.observed = []
                super().__init__(14)
            def feedback(self):
                if self.attempt >= 2:
                    self.bias = 0
                sample = super().feedback()
                self.observed.append(dict(sample['present_raw']))
                return sample
        io = DisappearingBiasArm()
        self.prepare(io)
        self.assertTrue(position_check(io.checked()['present_raw'])['home_converged'])
        self.assertTrue(all(position_check(raw)['safe'] for raw in io.observed))
        self.assert_bounds_and_scope(io)

    def test_config_cannot_expand_trim_axes_amount_or_stability(self):
        original = json.loads(json.dumps(load()))
        for key, value in (('axes', ['shoulder_lift', 'wrist_roll']),
                           ('max_step_ticks', 9), ('max_total_ticks', 17),
                           ('stable_s', .1), ('stable_span_ticks', 3)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                config = json.loads(json.dumps(original))
                config['startup']['home_trim'][key] = value
                path = Path(directory) / 'config.yaml'
                path.write_text(json.dumps(config), encoding='utf-8')
                with patch.multiple(config_runtime, PATH=path, _cached=None, _stamp=None):
                    with self.assertRaisesRegex(ValueError, '홈 명령 보상 범위 오류'):
                        config_runtime.load()

    def test_retry_starts_at_previous_command_without_reset_to_observation(self):
        io = BiasedHomeArm(15)
        self.prepare(io)
        boundaries = []
        for previous, row in zip(io.sent, io.sent[1:]):
            if row['attempt'] > previous['attempt']:
                boundaries.append((previous, row))
                self.assertLessEqual(abs(row['raw']['shoulder_lift'] - previous['raw']['shoulder_lift']), 1)
            if row['attempt'] >= 2:
                elapsed = row['time'] - previous['time']
                # Integer encoding permits one tick beyond the continuous bound.
                budget = 10 * load()['speed'] * elapsed * 4095 / 360 + 1
                self.assertLessEqual(abs(row['raw']['shoulder_lift'] - previous['raw']['shoulder_lift']), budget + 1e-6)
        self.assertTrue(boundaries)

    def test_frozen_lift_fails_with_bounded_trim_after_three_attempts(self):
        io = BiasedHomeArm(14, frozen=True)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            self.prepare(io)
        self.assertEqual(sum(row['event'] == 'startup_home_attempt' for row in io.events), 3)
        self.assertEqual(round(io.present['shoulder_lift']), 856)
        self.assert_bounds_and_scope(io)

    def test_moving_residual_does_not_accumulate_trim(self):
        io = BiasedHomeArm(30, moving=True)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            self.prepare(io)
        self.assertEqual(getattr(io, 'home_command_trim', {}).get('shoulder_lift', 0), 0)
        self.assert_bounds_and_scope(io)

    def test_gapped_samples_cannot_establish_stable_trim_evidence(self):
        class GappedArm(BiasedHomeArm):
            def checked(self):
                self.pause(.4)  # Larger than the unchanged 0.35s freshness bound.
                return super().checked()
            start_snapshot = checked
        io = GappedArm(14, frozen=True)
        with self.assertRaisesRegex(base.Stop, '홈 이동 후 위치 확인 실패'):
            self.prepare(io)
        self.assertEqual(getattr(io, 'home_command_trim', {}).get('shoulder_lift', 0), 0)
        self.assert_bounds_and_scope(io)

    def test_protection_and_interrupt_are_not_swallowed_by_trim(self):
        for fault, error in (('temperature', base.Stop), ('status', base.Stop),
                             ('communication', OSError), ('ctrl_c', KeyboardInterrupt)):
            with self.subTest(fault=fault):
                io = BiasedHomeArm(14, fault=fault)
                with self.assertRaises(error):
                    self.prepare(io)
                self.assertEqual(len(io.sent), 5)
                self.assertEqual(getattr(io, 'home_command_trim', {}).get('shoulder_lift', 0), 0)
                self.assert_bounds_and_scope(io)

    def test_home_recovery_enters_same_policy_loop(self):
        io = BiasedHomeArm(15)
        def policy(frames, state, origin, delay):
            return [io.home[:] for _ in range(50)], .02
        result = start_policy_session(io, policy, io.home, io.record, .3, .33)
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertGreater(result['steps'], 0)
        self.assertTrue(any(row['event'] == 'startup_home_converged' for row in io.events))


if __name__ == '__main__':
    unittest.main()
