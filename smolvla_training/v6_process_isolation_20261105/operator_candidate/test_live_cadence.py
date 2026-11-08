import unittest
from first_trial_core import *
from cadence_repeat import continuity
from test_first_trial import Hardware,Clock,HOME,BOUNDS,EVIDENCE,instant
from controller_bridge import load_controller

class Tests(unittest.TestCase):
    def run_case(self,stall=False,slow=False):
        clock=Clock();hw=Hardware(clock);snap=hw.snapshot;send=hw.send_target
        def snapshot():clock.now+=.04 if slow else .01;return snap()
        def sent(target):send(target);clock.now+=.004
        hw.snapshot=snapshot;hw.send_target=sent
        def sleep(n):clock.now+=n+(.04 if stall else 0)
        adapter=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS,waiter=instant,clock=clock,sleep=sleep);adapter.compensated=True
        return hw,adapter,load_controller(mock_posix=True)(adapter)
    def test_complete_ten_targets_with_compensated_intervals(self):
        hw,a,c=self.run_case();c.run(FirstMotionPlan(False));self.assertEqual(len(a.sent),10)
        for i in range(1,10):
            interval=a.sent[i]["sent_at"]-a.sent[i-1]["sent_at"]
            if i!=5:self.assertAlmostEqual(interval,1/30)
            else:self.assertGreaterEqual(interval,1/30)
        self.assertEqual(len(a.step_timings),10);self.assertTrue(hw.closed)
    def test_overslept_state_holds_before_next_target(self):
        hw,a,c=self.run_case(stall=True)
        with self.assertRaisesRegex(Hold,"관측 지연"):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),1);self.assertTrue(hw.closed)
    def test_slow_reads_do_not_catch_up_in_bursts(self):
        hw,a,c=self.run_case(slow=True);c.run(FirstMotionPlan(False))
        self.assertTrue(all(a.sent[i]["sent_at"]-a.sent[i-1]["sent_at"]>=.04 for i in range(1,10)))
    def test_continuity_accepts_known_goal_with_bounded_tracking_error(self):
        r=continuity([0.]*6,[.25]*5+[.2],[.26]*5+[.21],[.088]*6,BOUNDS)
        self.assertTrue(r["passed"])
    def test_unknown_goal_or_large_tracking_error_refuses(self):
        self.assertFalse(continuity([0.]*6,[.6]*5+[0.],[.6]*5+[0.],[.088]*6,BOUNDS)["passed"])
        self.assertFalse(continuity([0.]*6,[.2]*5+[0.],[.4]*5+[0.],[.088]*6,BOUNDS)["passed"])

if __name__=="__main__":unittest.main()
