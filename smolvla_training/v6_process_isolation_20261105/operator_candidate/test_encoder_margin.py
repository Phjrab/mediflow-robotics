import math,unittest
from first_trial_core import target_gate,Limits,Hold
from cadence_repeat import continuity

B=[(-180.,180.)]*5+[(0.,100.)]
Q=[360/4095]*5+[.06119951040391758]
class Tests(unittest.TestCase):
    def test_known_integer_goal_is_allowed_with_one_count_tracking_allowance(self):
        measured=[0.]*5+[50.];active=[0.,0.,0.,-.5274725274725327,0.,50.];expected=[0.,0.,0.,-.4581422491388025,0.,50.]
        self.assertFalse(continuity(measured,active,expected,Q,B)["passed"])
        self.assertTrue(continuity(measured,active,expected,Q,B,encoding_allowance=True)["passed"])
    def test_excess_tracking_or_unknown_goal_still_refuses(self):
        m=[0.]*5+[50.];a=[.62]*5+[50.]
        self.assertFalse(continuity(m,a,a,Q,B,encoding_allowance=True)["passed"])
        self.assertFalse(continuity(m,[.2]*5+[50.],[.4]*5+[50.],Q,B,encoding_allowance=True)["passed"])
    def test_integer_encoded_targets_remain_inside_actual_step_cap_for_both_directions(self):
        for phase in (0.,.3,.9):
            measured=[phase*q+10*q for q in Q];measured[5]=50.
            for sign in (-1,1):
                action=[v+sign for v in measured]
                target=target_gate(action,measured,measured,B,Limits(),1/30,quantum=Q)
                for i in range(6):
                    encoded=measured[i]+math.floor((target[i]-measured[i])/Q[i])*Q[i]
                    self.assertLessEqual(abs(encoded-measured[i]),(.5 if i<5 else 2.)+1e-9)
    def test_missing_or_excessive_resolution_refuses(self):
        for q in ([0.]*5,[0.]*6,[float("nan")]*6,[.6]*6):
            with self.assertRaises(Hold):target_gate([1.]*5+[50.],[0.]*5+[50.],[0.]*5+[50.],B,Limits(),1/30,quantum=q)
    def test_quantized_controller_completes_mock_ten_steps_without_register_writes(self):
        from test_live_cadence import Tests as LiveTests
        from first_trial_core import FirstMotionPlan
        hw,adapter,controller=LiveTests().run_case()
        hw.data["state"][5]=50.
        for action in hw.actions:action[5]=50.1
        hw.motor_quantum=lambda:Q
        adapter.quantized=True;controller.run(FirstMotionPlan(False))
        self.assertEqual(len(adapter.sent),10);self.assertEqual(hw.reg_writes,0)
        self.assertEqual(adapter.policy_chunk["motor_quantum"],Q)

if __name__=="__main__":unittest.main()
