import unittest
from first_trial_core import *
from work_segment_limits import WorkSegmentLimits
from test_first_trial import Clock,Hardware,HOME,BOUNDS,EVIDENCE,instant
from controller_bridge import load_controller

class Tests(unittest.TestCase):
    def test_work_limits_preserve_old_profile_and_reject_expansion(self):
        self.assertEqual(Limits().max_steps,10);WorkSegmentLimits().validate()
        for kwargs in (dict(cap=2.1),dict(max_steps=51),dict(max_seconds=11),dict(speed=21),dict(stop_temperature=56)):
            with self.subTest(kwargs=kwargs),self.assertRaises(Hold):WorkSegmentLimits(**kwargs).validate()
    def test_fifty_mock_goals_no_register_writes(self):
        clock=Clock();hw=Hardware(clock);a=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS,limits=WorkSegmentLimits(),waiter=instant,clock=clock,sleep=clock.sleep)
        a.compensated=True;a.clip_warning_only=True
        load_controller(mock_posix=True)(a).run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),50);self.assertEqual(hw.reg_writes,0);self.assertTrue(hw.closed)
        self.assertTrue(all(abs(r['target'][i]-r['state'][i])<=2 for r in a.sent for i in range(5)))
    def test_speed_still_limits_first_target_despite_larger_error_cap(self):
        t=target_gate([2.]*5+[0.],[0.]*6,[0.]*6,BOUNDS,WorkSegmentLimits(),1/30)
        for v in t[:5]:self.assertAlmostEqual(v,20/30)
    def test_bad_prediction_still_refuses(self):
        for v in ([float('nan')]*6,[11.]*5+[0.]):
            with self.assertRaises(Hold):target_gate(v,[0.]*6,[0.]*6,BOUNDS,WorkSegmentLimits(),1/30)

if __name__=="__main__":unittest.main()
