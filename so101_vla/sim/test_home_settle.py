"""Check the effective home windows and the terminal outcome formatter."""
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from config_runtime import criteria, load
from run_exit_report import describe, report
from start_position_rules import HomeConvergence, home_targets, position_check


class TrainingHomeWindowTests(unittest.TestCase):
    def test_effective_ranges_apply_observed_safety_before_admitting_home(self):
        limits = {j: v['home_range_ticks'] for j, v in criteria().items()}
        self.assertEqual(limits, {
            'shoulder_pan': [2035, 2081], 'shoulder_lift': [819, 865],
            'elbow_flex': [3058, 3087], 'wrist_flex': [2551, 2597],
            'wrist_roll': [1815, 1861], 'gripper': [450, 514],
        })
        for joint, (lo, hi) in limits.items():
            for value, accepted in ((lo, True), (hi, True), (lo-1, False), (hi+1, False)):
                with self.subTest(joint=joint, value=value):
                    result = position_check({**home_targets(), joint: value})
                    self.assertEqual(result['home_converged'], accepted)

    def test_training_observation_cap_not_pass_for_972(self):
        result = position_check({**home_targets(), 'shoulder_lift': 972})
        self.assertTrue(result['safe'])
        self.assertFalse(result['home_converged'])

    def test_timing_not_shortened_for_new_window(self):
        sample = {**home_targets(), 'shoulder_lift': 856}
        window = HomeConvergence()
        self.assertFalse(window.observe(sample, 0)['ready'])
        self.assertFalse(window.observe(sample, .25)['ready'])
        self.assertFalse(window.observe(sample, .49)['ready'])
        self.assertTrue(window.observe(sample, .5)['ready'])

    def test_requested_gripper_cap(self):
        c = load()
        self.assertEqual(c['gripper']['max_open_points'], 48.5)
        self.assertEqual(criteria(c)['gripper']['command_range_ticks'][1], 1205)


class ExitReportTests(unittest.TestCase):
    def test_home_hold_shows_joint_and_ticks_on_one_line(self):
        data = {'joints': {'shoulder_lift': {
            'present_ticks': 866, 'target_ticks': 842, 'converged': False}}}
        reason = '홈 이동 후 위치 확인 실패: '+json.dumps(data)
        self.assertEqual(describe({'status': 'HOLD', 'reason': reason}),
                         'HOLD home shoulder_lift 866/842')

    def test_receipt_points_to_this_run_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root)
            launch = parent/'launch'
            launch.mkdir()
            run = parent/'recent run'
            run.mkdir()
            (launch/'run_path').write_text(str(run), encoding='utf-8')
            (run/'summary.json').write_text(json.dumps({'status':'HOLD','reason':'serial timeout'}))
            console = StringIO()
            with redirect_stdout(console):
                report(launch)
            self.assertEqual(console.getvalue().splitlines(), ['HOLD serial timeout', f'실행 폴더 {run}'])

    def test_preflight_exit_is_reported_before_a_run_exists(self):
        with tempfile.TemporaryDirectory() as root:
            launch = Path(root)
            (launch/'end_stage').write_text('EXIT sudo 인증 (코드 1)\n', encoding='utf-8')
            console = StringIO()
            with redirect_stdout(console):
                report(launch)
            self.assertEqual(console.getvalue().splitlines(), ['EXIT sudo 인증 (코드 1)', f'실행 폴더 {launch}'])
