import unittest
from first_trial_core import *
from test_first_trial import Clock,Hardware,HOME,BOUNDS,EVIDENCE,instant
from controller_bridge import load_controller

class Tests(unittest.TestCase):
    def test_warning_trial_finishes_ten_capped_targets(self):
        clock=Clock();hw=Hardware(clock);hw.actions=[[9.]*5+[0.] for _ in range(50)]
        a=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS,waiter=instant,clock=clock,sleep=clock.sleep);a.clip_warning_only=True
        load_controller(mock_posix=True)(a).run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),10);self.assertIn("clip_ratio_warning",a.events)
        self.assertTrue(all(abs(r['target'][i]-r['state'][i])<=.5 for r in a.sent for i in range(5)))
        self.assertTrue(hw.closed);self.assertEqual(hw.reg_writes,0)
    def test_warning_cannot_bypass_big_jump(self):
        clock=Clock();hw=Hardware(clock);hw.actions[0][0]=11.
        a=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS,waiter=instant,clock=clock,sleep=clock.sleep);a.clip_warning_only=True
        with self.assertRaises(Hold):load_controller(mock_posix=True)(a).run(FirstMotionPlan(False))
        self.assertEqual(hw.targets,[]);self.assertTrue(hw.closed)

if __name__=="__main__":unittest.main()
