import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'live'))
import entry


class OvernightCLI(unittest.TestCase):
    def dispatch(self, *args):
        with patch.object(sys, 'argv', ['entry.py', *args]), patch.object(sys, 'addaudithook'):
            return entry.main()

    def test_replay_dry_routes_without_policy(self):
        with patch('episode_replay.run_episode', return_value=0) as run:
            self.assertEqual(self.dispatch('--dry-run', '--replay', '93', '--speed', '.33', '--duration', '180'), 0)
            run.assert_called_once_with(93, 180, .33, dry_run=True)

    def test_new_policy_options_reach_dry_runner(self):
        with patch('startup_simulation.run_dry', return_value=0) as run:
            self.dispatch('--mode', 'legacy', '--dry-run', '--assist-grasp', '--vel-limit', 'data',
                          '--speed', '.5', '--duration', '60')
            run.assert_called_once_with(60, .5, assist_grasp=True, vel_limit='data')

    def test_incompatible_and_unconfirmed_options_fail_before_dispatch(self):
        cases = [('--mode', 'raw', '--assist-grasp'), ('--replay', '93', '--assist-grasp'),
                 ('--replay', '93', '--speed', '.33', '--duration', '181'),
                 ('--replay', '137'), ('--replay', '93', '--speed', '.33', '--live')]
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                self.dispatch(*args)
            self.assertEqual(exc.exception.code, 2)
