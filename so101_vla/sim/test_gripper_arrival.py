import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gripper_arrival import AXES, GripperArrivalGate


class ArrivalTests(unittest.TestCase):
    def test_each_axis_uses_latest_knot_and_inclusive_absolute_tolerance(self):
        present = [0, 0, 0, 0, 99, 25]
        selected = [0, 0, 0, 0, 0, 12]
        for i, axis in enumerate(AXES):
            g = GripperArrivalGate()
            knot = selected.copy()
            knot[i] = -3.01
            self.assertEqual(g.apply(selected, present, 1., policy_knot=knot), 25)
            self.assertAlmostEqual(g.events[0]['policy_minus_present_deg'][axis], -3.01)
            knot[i] = -3.
            self.assertEqual(g.apply(selected, present, 1.2, policy_knot=knot), 12)
            self.assertEqual(g.summary()['outcomes']['arrived'], 1)

    def test_timeout_one_episode_then_reopen_and_close_again(self):
        g = GripperArrivalGate()
        present = [0, 0, 0, 0, 0, 25]
        def apply(points, t):
            return g.apply([7, 0, 0, 0, 0, points], present, t)
        self.assertEqual(apply(20, 0), 25)
        self.assertEqual(apply(18, 1.499), 25)
        self.assertEqual(apply(17, 1.5), 17)
        self.assertEqual(apply(15, 2.), 15)
        self.assertEqual(apply(26, 2.1), 26)
        self.assertEqual(apply(14, 2.2), 25)
        self.assertEqual(apply(26, 2.5), 26)
        self.assertEqual(apply(11, 3.), 25)
        g.finish(3.2)
        s = g.summary()
        self.assertEqual(s['waits'], 3)
        self.assertEqual(s['outcomes'], dict(arrived=0, timeout=1, cancelled=1, session_end=1))
        self.assertAlmostEqual(s['total_wait_s'], 2.)

    def test_feedback_drift_cannot_cancel_and_prior_alignment_cannot_authorize_new_close(self):
        g = GripperArrivalGate()
        self.assertEqual(g.apply([0,0,0,0,0,24.95], [0,0,0,0,0,25], 0), 24.95)
        model = [0,0,6,0,0,24.94]
        self.assertEqual(g.apply(model, [0,0,0,0,0,25], .1,
                                 requested_ticks=899, present_ticks=900), 25)
        self.assertEqual(g.apply(model, [0,0,0,0,0,24], .4,
                                 requested_ticks=899, present_ticks=880), 25)
        self.assertEqual(g.apply(model, [0,0,6,0,0,24], .6,
                                 requested_ticks=899, present_ticks=880), 24.94)
        self.assertEqual(g.events[-1]['decision'], 'arrived')

    def test_one_tick_closure_and_exact_hold_respect_downstream_limits(self):
        g = GripperArrivalGate()
        # The physical SDK can truncate a normalized round trip to the previous tick.
        self.assertEqual(g.apply([9,0,0,0,0,24.999], [0,0,0,0,0,25.0], 0.,
                                 requested_ticks=899, present_ticks=900), 25)
        def plan(value=24.94):
            return dict(wire_raw={'gripper':899}, wire_normalized=[0]*5+[value])
        same = plan()
        g.preserve_held_tick(same, 25., (413,1205))
        self.assertEqual((same['wire_raw']['gripper'], same['wire_normalized'][-1]), (900,25.))
        for protected, bounds in [(24.94,(413,1205)), (25.,(413,899))]:
            limited = plan()
            g.preserve_held_tick(limited, protected, bounds)
            self.assertEqual(limited['wire_raw']['gripper'], 899)
        # No physical closure at the same encoded tick.
        g = GripperArrivalGate()
        g.apply([9,0,0,0,0,24.999], [0,0,0,0,0,25], 0., requested_ticks=900, present_ticks=900)
        self.assertEqual(g.summary()['waits'], 0)

    def test_existing_open_cap_and_load_freeze_take_priority_over_held_tick(self):
        from gripper_safety import OpeningGuard
        opening = OpeningGuard()
        for held, protected in [(50., 48.5), (30., 26.)]:
            g = GripperArrivalGate()
            requested = [7,0,0,0,0,12.]
            points = g.apply(requested, [0,0,0,0,0,held], 0., requested_ticks=600, present_ticks=1200)
            load = opening.settings['opening_load_sign'] * opening.settings['opening_load_threshold_raw']
            for now in (0., .31):
                limited = opening.apply(points, 26. if held==30. else 50., load, now)
            self.assertEqual(limited, protected)
            planned = dict(wire_raw={'gripper':1100}, wire_normalized=[0]*5+[limited])
            g.preserve_held_tick(planned, limited, (413,1205))
            self.assertEqual(planned['wire_raw']['gripper'],1100)

    def test_unblended_upper_survives_overlap_on_selected_indices_only(self):
        from stream_live import merge
        original = [[0.]*6 for _ in range(8)]
        incoming = [[20.]*6 for _ in range(6)]
        slots = dict(enumerate(original))
        raw = {i:(0,tuple(row)) for i,row in enumerate(original)}
        merge(slots, incoming, 2, 3, raw_knots=raw)
        self.assertEqual(raw[2], (0,(0.,)*6))
        self.assertEqual(raw[3], (2,(20.,)*6))
        self.assertLess(slots[3][0], raw[3][1][0])
        self.assertEqual(merge(slots, incoming, 1, 8, raw_knots=raw), 0)
        self.assertEqual(raw[3], (2,(20.,)*6))

    def test_legacy_and_raw_dry_preflight_failure_record_empty_summary(self):
        import raw_live
        import startup_simulation as sim
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/'runs').mkdir()
            with patch.object(sim, '__file__', str(root/'sim/startup_simulation.py')), \
                 patch.object(raw_live, 'ROOT', root), \
                 patch.object(raw_live, 'load_reference', side_effect=ValueError('fixture input error')), \
                 patch('trial_runtime.static_context', side_effect=ValueError('fixture input error')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sim.run_dry(1, .5), 1)
                self.assertEqual(raw_live.run_dry(1), 1)
            summaries = [json.loads(p.read_text(encoding='utf8')) for p in (root/'runs').glob('*/summary.json')]
            self.assertEqual(len(summaries), 2)
            for summary in summaries:
                self.assertEqual(summary['reason'], 'fixture input error')
                self.assertEqual(summary['gripper_close_wait']['waits'], 0)

    def test_legacy_gap_sends_and_records_one_protected_close_without_advancing_arm(self):
        from unittest.mock import MagicMock
        from config_runtime import load
        from startup_simulation import SimulatedArm
        from stream_live import stream
        home = load()['home']['state']
        rows = []
        arm = SimulatedArm(home, {}, rows.append, 'camera_dry')
        arm.policy_active = True
        arm.out = 'fixture'
        recorder = MagicMock()
        recorder.due.return_value = False
        recorder.goal.return_value = None
        recorder.close.return_value = dict(event_count=0, events=[])
        original_read = arm.checked
        def checked():
            sample = original_read()
            pose = arm.plan(home[:5]+[25.])
            sample.update(state=pose['wire_normalized'], present_raw=pose['wire_raw'])
            return sample
        arm.checked = checked
        def policy(frames, state, origin, delay):
            values = []
            for i in range(origin, origin+50):
                value = list(home)
                value[0] += 8 if i >= 45 else 0
                value[-1] = 15 if i >= 45 else 27
                values.append(value)
            return values, .005 if origin == 0 else 3.
        with patch('stream_live.GraspRecorder', return_value=recorder), contextlib.redirect_stdout(io.StringIO()):
            result = stream(arm, policy, rows.append, 5.5, 1.)
        self.assertEqual(result['status'], 'RUN_COMPLETE', result['reason'])
        gate = result['gripper_close_wait']
        timeout = next(e for e in gate['events'] if e['decision']=='timeout')
        self.assertLessEqual(timeout['wait_s'], 1.5 + .05)
        self.assertTrue(any(r['event']=='loop_timing' and r['action_starved']
                            and abs(r['time_s']-timeout['time_s']) < .05 for r in rows))
        goals = [r for r in rows if r['event']=='goal_transmitted']
        previous = [r for r in goals if r['time_s'] < timeout['time_s']][-1]
        following = next(r for r in goals if r['time_s'] > timeout['time_s'])
        self.assertTrue(previous['gripper_waiting'])
        self.assertFalse(following['gripper_waiting'])
        release, = [r for r in rows if r['event']=='gripper_safety_goal']
        self.assertEqual(result['gripper_close_gap_packets'], 1)
        self.assertLess(release['time_s'], following['time_s'])
        self.assertEqual(release['step'], previous['step'])
        self.assertFalse(release['policy_index_advanced'])
        self.assertEqual(release['model_phase'], previous['model_phase'])
        self.assertLess(release['wire_raw']['gripper'], previous['wire_raw']['gripper'])
        for key in previous['wire_raw'].keys()-{'gripper'}:
            self.assertEqual(release['wire_raw'][key], previous['wire_raw'][key])
        recorded, = [c for c in recorder.goal.call_args_list if c.args[1]['event']=='gripper_safety_goal']
        self.assertEqual(recorded.args[0], release['wire_normalized'][-1])
        self.assertEqual(recorded.kwargs['time_s'], release['time_s'])
        self.assertLess(following['wire_raw']['gripper'], previous['wire_raw']['gripper'])
        first = next(r for r in goals if r['gripper_waiting'])
        self.assertEqual(first['raw_upper_index'], 45)
        self.assertGreater(first['raw_upper'][0] - first['feedback']['state'][0], 3.)
