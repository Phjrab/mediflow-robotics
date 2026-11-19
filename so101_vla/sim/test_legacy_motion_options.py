"""Exercise the legacy opt-ins with in-memory feedback and packets only."""
import contextlib
import io
import json
import math
import unittest
from unittest.mock import patch

from direct_live import JOINTS
from legacy_motion_options import ASSIST_LABEL, GraspAssist, REFERENCE, WireRateLimit, load_options
from startup_simulation import CONFIG, SimulatedArm, TICK_SIZE, run_case
from stream_live import stream


class LegacyMotionOptionTests(unittest.TestCase):
    def setUp(self):
        self.ref = json.loads(REFERENCE.read_text(encoding='utf-8'))
        self.median = self.ref['close_median']

    def test_default_ignores_missing_file_and_unknown_mode_fails(self):
        with patch('legacy_motion_options.REFERENCE', None):
            self.assertIsNone(load_options(False, None, .5))
        with self.assertRaises(ValueError):
            load_options(False, 'fast', .5)

    def test_dataset_speed_applied_once(self):
        for speed in (.33, .5, 1.):
            result = load_options(False, 'data', speed)
            for joint, cap in zip(JOINTS, result.rates):
                self.assertAlmostEqual(cap, self.ref['speed_abs']['action'][joint]['p99']*speed/self.ref['speed'])

    def test_assist_requires_all_three_poses_and_fresh_closing_intent(self):
        for source in ('policy', 'knot', 'state'):
            poses = {key: self.median.copy() for key in ('policy', 'knot', 'state')}
            poses['state'][-1] += 5.
            poses[source][4] += 5.01
            assist = GraspAssist(self.median)
            row = assist.apply(poses['policy'], poses['knot'], {'state': poses['state']}, 0.)
            self.assertFalse(row['holding'])
            self.assertEqual(assist.windows, 0)
        assist = GraspAssist(self.median)
        state = [*self.median[:-1], 42.]
        sample = dict(state=state, present_raw={'gripper': 800})
        policy = [*(v+5 for v in self.median[:5]), 38.]
        for now in (0., .01, .12, .20, .30):
            row = assist.apply(policy, policy, sample, now)
            self.assertEqual(row['holding'], now < .30)
            self.assertTrue(all(-5 <= value <= 0 for value in row['offset_deg']))
        self.assertEqual(row['offset_deg'], [-5.]*5)
        for now, grip in ((.31, 39.), (.32, 39.5), (.33, 43.)):
            policy[-1] = grip
            row = assist.apply(policy, policy, sample, now)
            self.assertFalse(row['holding'])
            self.assertEqual(row['offset_deg'], [0.]*5)
        self.assertEqual(assist.windows, 1)

    def test_integrated_assist_preserves_policy_gate_and_final_packet(self):
        arm = SimulatedArm(CONFIG['home']['state'], {'mock': True}, lambda _: None, 'normal')
        pose = [v+2. for v in self.median[:5]]
        arm.present = arm.plan([*pose, 42.])['wire_raw']
        arm.target = arm.present.copy()
        first_grip = arm.present['gripper']
        arm.policy_active = True
        goal = [*pose, 31.]
        rows = []
        options = load_options(True, 'data', .5)
        with contextlib.redirect_stdout(io.StringIO()):
            result = stream(arm, lambda *_: ([goal[:] for _ in range(50)], .01), rows.append, .9,
                            speed=.5, motion_options=options)
        self.assertEqual(result['status'], 'RUN_COMPLETE', result['reason'])
        self.assertEqual(result['grasp_assist']['windows'], 1)
        packets = [r for r in rows if r['event'] == 'goal_transmitted']
        self.assertTrue(all(r['model'] == goal and list(r['raw_upper']) == goal for r in packets))
        for r in packets:
            self.assertEqual(r['motion_options']['run_label'], ASSIST_LABEL)
            self.assertLessEqual(max(map(abs, r['grasp_assist']['offset_deg'])), 5.)
            if r['grasp_assist']['holding'] or r['gripper_waiting']:
                self.assertEqual(r['wire_raw']['gripper'], first_grip)
        self.assertTrue(any(r['wire_raw']['gripper'] < first_grip for r in packets))
        self.assertTrue(any(abs(r['grasp_assist']['offset_deg'][0]) > 1. for r in packets))
        self.assertEqual(arm.packet_count, len(packets))

    def test_velocity_limit_integer_bounds_and_no_idle_credit(self):
        arm = SimulatedArm(CONFIG['home']['state'], {}, lambda _: None, 'normal')
        limiter = WireRateLimit(arm, [size*45 for size in TICK_SIZE])
        sample = arm.feedback()
        start = sample['present_raw']
        target = {j: min(arm.raw_bounds[j][1], start[j]+50) for j in JOINTS}
        previous = start
        for tick in range(121):
            plan = {'wire_raw': (start if tick < 60 else target).copy()}
            limiter.apply(plan, sample, previous, 1/60, protect_gripper=False)
            for j in JOINTS:
                self.assertLessEqual(abs(plan['wire_raw'][j]-previous[j]), 1)
                self.assertLessEqual(plan['wire_raw'][j]-start[j], math.ceil(max(0, tick-59)*.75))
                self.assertLessEqual(plan['wire_raw'][j], target[j])
            previous = plan['wire_raw']
        self.assertGreater(previous['shoulder_pan'], start['shoulder_pan']+40)

    def test_assist_does_not_release_an_unarrived_policy_knot(self):
        arm = SimulatedArm(CONFIG['home']['state'], {'mock': True}, lambda _: None, 'normal')
        arm.policy_active = True
        actual = arm.plan([*self.median[:5], 42.])['wire_raw']
        arm.present, arm.target = actual.copy(), actual.copy()
        send = arm.send
        def frozen_arm(raw):
            send(raw)
            arm.target = actual.copy()
        arm.send = frozen_arm
        goal = [self.median[0]+4., *self.median[1:5], 32.]
        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            result = stream(arm, lambda *_: ([goal[:] for _ in range(50)], .01), rows.append, 1.8,
                            speed=.5, motion_options=load_options(True, 'data', .5))
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertEqual(result['grasp_assist']['windows'], 1)
        self.assertEqual(result['gripper_close_wait']['outcomes']['timeout'], 1)
        held = [r for r in rows if r['event'] == 'goal_transmitted' and r['gripper_waiting']]
        self.assertGreater(held[-1]['time_s']-held[0]['time_s'], 1.4)
        self.assertTrue(all(r['wire_raw']['gripper'] == actual['gripper'] for r in held))
        self.assertTrue(any(r['event'] == 'goal_transmitted' and r['wire_raw']['gripper'] < actual['gripper']
                            for r in rows))

    def test_mock_receipt_and_safety_preflight(self):
        opts = load_options(True, 'data', .33)
        home = CONFIG['home']['state']
        def policy(_frames, state, *_):
            return [state[:] for _ in range(50)], .01
        with contextlib.redirect_stdout(io.StringIO()):
            for case in ('partial_off', 'overheat', 'elbow_plus_5'):
                result = run_case(case, 2, .33, home, {'mock': True}, policy, motion_options=opts)
                self.assertTrue(result['passed'], result)
                self.assertEqual(result['physical_motor_writes'], 0)
                self.assertEqual(result['motion_options']['run_label'], ASSIST_LABEL)

    def test_assist_close_during_policy_gap_only_changes_gripper(self):
        arm = SimulatedArm(CONFIG['home']['state'], {'mock': True}, lambda _: None, 'normal')
        arm.policy_active = True
        pose = [*self.median[:5], 42.]
        arm.present = arm.plan(pose)['wire_raw']
        arm.target = arm.present.copy()
        rows = []
        def policy(_frames, _state, origin, _delay):
            return [[*pose[:5], 32. if origin+i >= 47 else 42.] for i in range(50)], .01 if origin == 0 else 3.
        with contextlib.redirect_stdout(io.StringIO()):
            result = stream(arm, policy, rows.append, 2.6, speed=1., motion_options=load_options(True, 'data', 1.))
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        release, = [r for r in rows if r['event'] == 'gripper_safety_goal']
        prior = [r for r in rows if r['event'] == 'goal_transmitted' and r['time_s'] < release['time_s']][-1]
        self.assertEqual(release['reason'], 'grasp_assist_release')
        self.assertEqual(release['model_phase'], prior['model_phase'])
        self.assertFalse(release['grasp_assist']['holding'])
        self.assertTrue(prior['grasp_assist']['holding'])
        self.assertEqual(release['wire_normalized'][:5], prior['wire_normalized'][:5])
        self.assertLess(release['wire_raw']['gripper'], prior['wire_raw']['gripper'])


if __name__ == '__main__':
    unittest.main()
