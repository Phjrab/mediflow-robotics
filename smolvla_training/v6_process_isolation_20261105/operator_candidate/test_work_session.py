import contextlib,io,unittest
from first_trial_core import *
from work_session import SessionStages
from work_segment_limits import WorkSegmentLimits
from test_first_trial import Clock,Hardware,HOME,BOUNDS,EVIDENCE,instant
from controller_bridge import load_controller
from cadence_repeat import continuity

class MockSession(SessionStages,FirstTrialAdapter):pass
class Tests(unittest.TestCase):
    def build(self):
        clock=Clock();hw=Hardware(clock)
        a=MockSession(hw,EVIDENCE,HOME,BOUNDS,limits=WorkSegmentLimits(),waiter=instant,clock=clock,sleep=clock.sleep)
        a.compensated=True;a.clip_warning_only=True
        return hw,a,load_controller(mock_posix=True)(a)
    def test_six_chunks_three_hundred_targets_and_global_indices(self):
        hw,a,c=self.build()
        with contextlib.redirect_stdout(io.StringIO()):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),300);self.assertEqual([r['step'] for r in a.sent],list(range(300)))
        self.assertEqual(len(a.session_chunks),6);self.assertEqual(len(a.step_timings),300)
        self.assertTrue(hw.closed);self.assertEqual(hw.reg_writes,0)
    def test_prediction_failure_preserves_partial_chunks_and_closes(self):
        hw,a,c=self.build();original=hw.predict;count=[0]
        def fail(*args):
            count[0]+=1
            if count[0]==2:raise Hold('fixture stop')
            return original(*args)
        hw.predict=fail
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(Hold):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),50);self.assertEqual(a.session_chunks[-1]['status'],'HOLD');self.assertTrue(hw.closed)
        self.assertEqual(len(a.step_timings),50);self.assertEqual([r['step'] for r in a.step_timings],list(range(50)))
    def test_work_goal_continuity_has_two_degree_tracking_cap_but_keeps_identity(self):
        b=[(-180.,180.)]*5+[(0.,100.)];q=[.088]*6
        self.assertTrue(continuity([0.]*6,[1.5]*5+[0.],[1.5]*5+[0.],q,b,tracking_cap=2,encoding_allowance=True)['passed'])
        self.assertFalse(continuity([0.]*6,[1.5]*5+[0.],[1.8]*5+[0.],q,b,tracking_cap=2,encoding_allowance=True)['passed'])
    def test_budget_does_not_start_another_ten_second_chunk(self):
        hw,a,c=self.build();original=hw.predict
        def slow(*args):hw.clock.now+=1.4;return original(*args)
        hw.predict=slow
        send=hw.send_target
        def delayed_send(target):send(target);hw.clock.now+=.06
        hw.send_target=delayed_send
        with contextlib.redirect_stdout(io.StringIO()):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),250);self.assertEqual(a.session_end_reason,'TIME_BUDGET');self.assertTrue(hw.clock.now<31)
    def test_home_is_required_at_session_start_but_not_after_normal_motion(self):
        hw,a,c=self.build();calls=[0];original=hw.predict
        def moving(*args):
            calls[0]+=1
            return original(*args) if calls[0]==1 else [[6.]*5+[0.] for _ in range(50)]
        hw.predict=moving
        with contextlib.redirect_stdout(io.StringIO()):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.sent),300);self.assertGreater(hw.data['state'][0],5.)

if __name__=="__main__":unittest.main()
