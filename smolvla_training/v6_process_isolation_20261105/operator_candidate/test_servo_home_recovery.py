from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from first_trial_core import Hold, JOINTS
from servo_home_recovery import enable_and_record, home_rows


class ServoHomeRecoveryTests(TestCase):
    def setUp(self):
        self.raw = dict.fromkeys(JOINTS,2048)
        self.home = {"state":[0.]*6}
        self.baseline = dict(state=[0.]*6,ticks=self.raw,torque=dict.fromkeys(JOINTS,0))
        self.guard = SimpleNamespace(writes=[])
        self.calls = []

    def send(self, bus, guard, register, values, phase):
        guard.writes.append(phase)
        self.calls.append((register,dict(values),phase))

    def test_angular_and_gripper_home_limits(self):
        state={"state":[2.,-2.,0.,2.1,-2.1,2.1]}
        rows=home_rows(state,self.home)
        self.assertEqual([r["inside"] for r in rows.values()],[True,True,True,False,False,False])
        self.assertEqual(rows["gripper"]["unit"],"점")

    def test_goal_mismatch_never_enables_torque(self):
        with patch("servo_home_recovery.write",self.send), patch("servo_home_recovery.active_goals",return_value=self.raw):
            with self.assertRaisesRegex(Hold,"재확인 불일치"):
                enable_and_record(None,self.guard,lambda _:self.baseline,
                                  lambda:{**self.raw,"elbow_flex":2047},self.home,{},lambda _:None)
        self.assertEqual(self.calls,[
            ("Goal_Position",self.raw,"match_present"),
            ("Torque_Enable",dict.fromkeys(JOINTS,0),"safety_off"),
        ])

    def test_motion_excess_disables_torque_after_exact_goal(self):
        report={}
        def read(torque):
            if torque==1:
                return {**self.baseline,"state":[0.,0.,1.01,0.,0.,0.]}
            return self.baseline
        with patch("servo_home_recovery.write",self.send), patch("servo_home_recovery.active_goals",return_value=self.raw):
            with self.assertRaisesRegex(Hold,"이동 상한 초과"):
                enable_and_record(None,self.guard,read,lambda:self.raw,self.home,report,lambda _:None)
        self.assertEqual(self.calls,[
            ("Goal_Position",self.raw,"match_present"),
            ("Torque_Enable",dict.fromkeys(JOINTS,1),"enable"),
            ("Torque_Enable",dict.fromkeys(JOINTS,0),"safety_off"),
        ])
        self.assertTrue(report["safety_off_confirmed"])
