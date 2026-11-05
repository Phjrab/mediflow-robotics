import copy
import threading
import time
import unittest
from first_trial_core import *
from controller_bridge import load_controller
from bounded_wait import wait

HOME={"joint_names":list(JOINTS),"state":[0.]*6,"tolerance_deg":5.}
BOUNDS=[(-180.,180.)]*5+[(0.,100.)]  # 합성 fixture; 실기 한계로 사용하지 않는다.
EVIDENCE={"kind":"FIXTURE",**dict.fromkeys(("model_verified","units_verified","mapping_verified","operator_live_confirmed","arm_receipt_verified"),True)}

class Clock:
    def __init__(self):self.now=1.
    def __call__(self):return self.now
    def sleep(self,t):self.now+=t

class Hardware:
    physical=False
    def __init__(self,clock):
        self.clock=clock;self.closed=False;self.targets=[];self.reg_writes=0;self.count=0;self.frames=0
        self.data=dict(state=[0.]*6,torque=dict.fromkeys(JOINTS,1),modes=dict.fromkeys(JOINTS,0),
                       temperatures=dict.fromkeys(JOINTS,30.),loads=dict.fromkeys(JOINTS,0),statuses=dict.fromkeys(JOINTS,0),moving=False)
        self.actions=[[.1]*5+[0.] for _ in range(50)]
    def connect(self):pass
    def snapshot(self):return copy.deepcopy(self.data)
    def health_snapshot(self):return self.snapshot()
    def check_records(self):pass
    def capture_frames(self):
        self.frames+=1
        return {name:dict(host_received=self.clock(),shape=[480,640,3],dtype="uint8",sha256=str(self.frames)+name) for name in CAMERAS}
    def predict(self,frames,state):return copy.deepcopy(self.actions)
    def send_target(self,target):self.targets.append(target);self.data["state"]=list(target)
    def close(self):self.closed=True

def instant(fn,monitor,**kwargs):monitor();value=fn();monitor();return value

class ControllerTests(unittest.TestCase):
    def run_trial(self,hw,evidence=None,waiter=instant):
        adapter=FirstTrialAdapter(hw,evidence or EVIDENCE,HOME,BOUNDS,waiter=waiter,clock=hw.clock,sleep=hw.clock.sleep)
        controller=load_controller(mock_posix=True)(adapter)
        controller.run(FirstMotionPlan(physical_motion_authorized=False))
        return adapter
    def test_actual_controller_routes_mock_and_closes(self):
        clock=Clock();hw=Hardware(clock);adapter=self.run_trial(hw)
        self.assertEqual(len(hw.targets),10);self.assertTrue(hw.closed);self.assertEqual(hw.reg_writes,0)
        self.assertTrue(adapter.stop.is_set())
    def test_missing_live_evidence_refused(self):
        for key in ("model_verified","units_verified","mapping_verified","operator_live_confirmed","arm_receipt_verified"):
            clock=Clock();hw=Hardware(clock);evidence=dict(EVIDENCE);evidence[key]=False
            with self.subTest(key=key),self.assertRaises(Hold):self.run_trial(hw,evidence)
            self.assertTrue(hw.closed);self.assertEqual(hw.targets,[])
    def test_faults_hold_before_send(self):
        for kind in ("nan","dimension","gripper","jump","hot","overload","status","torque","mode","home"):
            clock=Clock();hw=Hardware(clock)
            if kind=="nan":hw.actions[0][0]=float("nan")
            if kind=="dimension":hw.actions[0]=[0.]
            if kind=="gripper":hw.actions[0][-1]=101.
            if kind=="jump":hw.actions[0][0]=11.
            if kind=="hot":hw.data["temperatures"][JOINTS[0]]=55.
            if kind=="overload":hw.data["loads"][JOINTS[0]]=200.
            if kind=="status":hw.data["statuses"][JOINTS[0]]=1
            if kind=="torque":hw.data["torque"][JOINTS[0]]=0
            if kind=="mode":hw.data["modes"][JOINTS[0]]=1
            if kind=="home":hw.data["state"][0]=6.
            with self.subTest(kind=kind),self.assertRaises(Hold):self.run_trial(hw)
            self.assertTrue(hw.closed);self.assertEqual(hw.targets,[])
    def test_stale_skew_and_repeated_frame_hold(self):
        for kind in ("stale","skew","repeat"):
            clock=Clock();hw=Hardware(clock);capture=hw.capture_frames
            def bad():
                frames=capture()
                if kind=="stale":frames[CAMERAS[0]]["host_received"]-=.3
                if kind=="skew":frames[CAMERAS[0]]["host_received"]-=.15
                if kind=="repeat":
                    for role in CAMERAS:frames[role]["sha256"]=role
                return frames
            hw.capture_frames=bad
            with self.subTest(kind=kind),self.assertRaises(Hold):self.run_trial(hw)
            self.assertEqual(hw.targets,[]);self.assertTrue(hw.closed)
    def test_stop_during_prediction_and_ctrlc_close_without_write(self):
        for error in (Hold("timeout"),KeyboardInterrupt()):
            clock=Clock();hw=Hardware(clock)
            def bad(*args):raise error
            hw.predict=bad
            with self.assertRaises(type(error)):self.run_trial(hw)
            self.assertEqual(hw.targets,[]);self.assertTrue(hw.closed)
    def test_55_during_wait_hold(self):
        clock=Clock();hw=Hardware(clock)
        def predict(*args):hw.data["temperatures"][JOINTS[0]]=55.;return hw.actions
        hw.predict=predict
        with self.assertRaises(Hold):self.run_trial(hw)
        self.assertEqual(hw.targets,[])
    def test_cap_and_velocity_gate(self):
        target=target_gate([1.]*5+[0.],[0.]*6,[0.]*6,BOUNDS,Limits(),1/30)
        self.assertEqual(target,[.5]*5+[0.])
        target=target_gate([1.]*5+[0.],[0.]*6,[0.]*6,BOUNDS,Limits(),.01)
        self.assertEqual(target,[.2]*5+[0.])
    def test_unverified_bounds_refused(self):
        with self.assertRaises(Hold):target_gate([0.]*6,[0.]*6,[0.]*6,[],Limits(),.03)
    def test_main_mode_mismatch_no_connect(self):
        clock=Clock();hw=Hardware(clock);adapter=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS)
        with self.assertRaises(Exception):load_controller(mock_posix=True)(adapter).run(FirstMotionPlan(True))
        self.assertEqual(hw.targets,[])
    def test_post_monitor_delay_is_in_deadline(self):
        calls=[]
        def monitor():
            calls.append(1)
            if len(calls)>1:time.sleep(.03)
        with self.assertRaises((Hold,RuntimeError)):
            wait(lambda:7,monitor,deadline=time.monotonic()+1,timeout=.015)

if __name__=="__main__":unittest.main()
