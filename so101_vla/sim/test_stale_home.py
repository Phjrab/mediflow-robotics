import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from legacy_motion_options import load_options, stale_home_return
from startup_simulation import CONFIG, SimulatedArm
from stream_live import start_policy_session, stream


class StaleHomeTests(unittest.TestCase):
    def test_optional_collection_precedes_home_checks_and_policy_control(self):
        for enabled in (False, True):
            order = []
            opts = load_options(False, None, .4, precollect=enabled)
            with patch('gc.collect', side_effect=lambda gen: order.append(('collect', gen)) or 0), \
                    patch('live_start.prepare', side_effect=lambda *_: order.append('home') or {}), \
                    patch('stream_live.stream', side_effect=lambda *a, **kw: order.append('stream')), \
                    contextlib.redirect_stdout(io.StringIO()):
                start_policy_session(SimpleNamespace(), None, self.home, lambda row: order.append(row['event']),
                                     3, .4, motion_options=opts)
            self.assertEqual(order, ([('collect', 2), 'startup_gc_complete'] if enabled else [])
                             + ['home', 'stream'])

    home = (0, -98, 96, 44, 0, 2)
    reached = (2, -30, 16, 65, -7, 39)

    def test_old_home_observation_cannot_retract_extended_arm(self):
        self.assertTrue(stale_home_return(self.home, self.home, self.reached, self.home, 1.48))

    def test_fresh_observation_can_command_a_return(self):
        self.assertFalse(stale_home_return(self.home, self.reached, self.reached, self.home, 1.48))
        self.assertFalse(stale_home_return(self.home, self.home, self.reached, self.home, .1))

    def test_approach_small_correction_and_stationary_arm_pass(self):
        for current, target in ((self.reached, self.reached), (self.home, self.home),
                                ((2, -30, 85, 65, -7, 39), self.home)):
            self.assertFalse(stale_home_return(self.home, self.home, current, target, 1.48))

    def test_option_is_independent_and_off_by_default(self):
        self.assertIsNone(load_options(False, None, .5))
        guard = load_options(False, None, .4, True)
        self.assertTrue(guard.reject_stale_home)
        self.assertFalse(guard.rates or guard.assist_grasp)

    def test_stream_rejects_before_merge_and_refreshes_from_extended_arm(self):
        home = CONFIG['home']['state']
        arm = SimulatedArm(home, {'mock': True}, lambda _: None, 'normal')
        arm.policy_active = True
        requests = []

        def policy(frames, state, origin, delay):
            requests.append((origin, tuple(state)))
            if len(requests) == 1:
                return [home if i < 30 else self.reached for i in range(50)], 1.5
            return [home] * 50, 1.5

        rows = []
        with contextlib.redirect_stdout(io.StringIO()):
            result = stream(arm, policy, rows.append, 7, speed=.5,
                            motion_options=load_options(False, None, .5, True))
        self.assertEqual(result['status'], 'RUN_COMPLETE', result['reason'])
        self.assertEqual(result['stale_home_guard']['rejected_chunks'], 1)
        self.assertGreater(result['stale_home_guard']['retained_tail_playback_s'], 1.)
        self.assertEqual(result['chunk_wait_s']['sum'], 0)
        rejected = next(r for r in rows if r['event'] == 'stale_home_rejected')
        self.assertEqual(requests[1][0], rejected['origin'])
        self.assertEqual(requests[2][1], tuple(rejected['present']))
        sent = [r for r in rows if r['event'] == 'goal_transmitted']
        self.assertFalse(any(r['raw_upper_origin'] == rejected['origin'] for r in sent))
        self.assertTrue(any(r['raw_upper_origin'] == requests[2][0] and list(r['raw_upper']) == home
                            for r in sent))
