import copy
import json
from pathlib import Path
import unittest
from first_trial_core import *
from test_first_trial import Clock,Hardware,instant,EVIDENCE
from controller_bridge import load_controller


def offline_file():
    p=Path(__file__).resolve().parents[2]/"action_bounds_hold_20261108/offline_result/OFFLINE_REVIEW.json"
    if p.exists():return p
    choices=sorted(Path.home().glob("action_bounds_review_*/offline_result/OFFLINE_REVIEW.json"))
    if not choices:raise FileNotFoundError("저장 프레임 오프라인 검토 fixture 없음")
    return choices[-1]


class Tests(unittest.TestCase):
    def setUp(self):
        self.bounds=[[-1.,1.]]+[[-180.,180.]]*4+[[0.,100.]]
        self.state=[.8,0.,0.,0.,0.,0.]

    def test_raw_policy_overshoot_projects_into_bounds_and_cap(self):
        action=[1.6,0.,0.,0.,0.,0.]
        target=target_gate(action,self.state,self.state,self.bounds,Limits(),1/30)
        self.assertEqual(target,[1.,0.,0.,0.,0.,0.]);self.assertEqual(action[0],1.6)

    def test_speed_limit_still_applies_to_projected_goal(self):
        target=target_gate([1.6,0.,0.,0.,0.,0.],self.state,self.state,self.bounds,Limits(),.001)
        self.assertAlmostEqual(target[0],.82)

    def test_current_state_outside_bounds_still_holds_with_joint_details(self):
        measured=list(self.state);measured[0]=1.1
        with self.assertRaises(Hold) as caught:target_gate(self.state,measured,self.state,self.bounds,Limits(),1/30)
        self.assertEqual(caught.exception.observed["source"],"measured_state")
        self.assertEqual(caught.exception.observed["joint"],"shoulder_pan")

    def test_previous_target_outside_bounds_still_holds(self):
        previous=list(self.state);previous[0]=1.1
        with self.assertRaises(Hold) as caught:target_gate(self.state,self.state,previous,self.bounds,Limits(),1/30)
        self.assertEqual(caught.exception.observed["source"],"previous_target")

    def test_large_jump_cannot_be_hidden_by_projection(self):
        with self.assertRaisesRegex(Hold,"큰 목표 점프"):target_gate([12.,0.,0.,0.,0.,0.],self.state,self.state,self.bounds,Limits(),1/30)

    def test_invalid_predictions_still_hold(self):
        for bad in ([float("nan")]*6,[float("inf")]*6,[0.]*5,[0.]*5+[101.]):
            with self.subTest(bad=bad),self.assertRaises(Hold):target_gate(bad,self.state,self.state,self.bounds,Limits(),1/30)

    def test_real_saved_predictions_are_projected_and_physical_goals_stay_bounded(self):
        data=json.loads(offline_file().read_text(encoding="utf-8"));state=data["source_state"];bounds=data["source_bounds"]
        checked=0
        for run in data["runs"]:
            for action in run["raw_actions"][:10]:
                target=target_gate(action,state,state,bounds,Limits(),1/30)
                bounds_gate(target,bounds)
                self.assertTrue(all(abs(target[i]-state[i])<=.500001 for i in range(5)))
                self.assertLessEqual(abs(target[5]-state[5]),2.)
                checked+=1
        self.assertEqual(checked,30)

    def test_controller_keeps_diagnostics_when_a_target_is_refused(self):
        clock=Clock();hw=Hardware(clock);hw.actions[0][0]=11.
        home=dict(joint_names=list(JOINTS),state=[0.]*6,tolerance_deg=5.)
        adapter=FirstTrialAdapter(hw,EVIDENCE,home,[(-180.,180.)]*5+[(0.,100.)],waiter=instant,clock=clock,sleep=clock.sleep)
        with self.assertRaises(Hold) as caught:load_controller(mock_posix=True)(adapter).run(FirstMotionPlan(False))
        self.assertEqual(adapter.policy_chunk["raw_actions"][0][0],11.)
        self.assertEqual(caught.exception.observed["target_context"]["step"],0)
        self.assertEqual(hw.targets,[]);self.assertTrue(hw.closed)


if __name__=="__main__":unittest.main()
