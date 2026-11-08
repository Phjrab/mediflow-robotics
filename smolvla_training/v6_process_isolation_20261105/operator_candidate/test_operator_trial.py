import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import arm_source as arm
from first_trial_core import JOINTS,Hold,vector,home_gate
import first_trial
from operator_arm import confirm,ArmingAdapter

HOME=dict(joint_names=list(JOINTS),state=[0.]*6,tolerance_deg=5.)

class Bus:
    def __init__(self):
        self.torque=dict.fromkeys(JOINTS,0);self.goal=dict.fromkeys(JOINTS,1000);self.current=dict(self.goal);self.writes=[]
        self.feedback=dict(self.current)
        self.motors={j:SimpleNamespace(id=i) for i,j in enumerate(JOINTS,1)}
        self.calibration={j:SimpleNamespace(range_min=100,range_max=3000) for j in JOINTS}
        self.port_handler=object()
        def read_target(port,motor,address,width):
            if (address,width)!=(67,2):raise AssertionError("목표 피드백 주소 오류")
            return list(self.feedback[JOINTS[motor-1]].to_bytes(2,"little")),0,0
        self.packet_handler=SimpleNamespace(_individual_reply_guard=SimpleNamespace(installed=True,fault=None),readTxRx=read_target)
    def read(self,register,joint,normalize=True,num_retry=0):
        if register=="Present_Position":return 0. if normalize else self.current[joint]
        if register=="Goal_Position":return self.goal[joint]
        if register=="Torque_Enable":return self.torque[joint]
        if register=="Operating_Mode":return 0
        if register=="Moving":return 0
        raise AssertionError(register)
    def sync_write(self,register,values,**kwargs):
        if register!="Torque_Enable":raise AssertionError("모의 시험에서도 목표 쓰기 금지")
        self.writes.append((register,dict(values)));self.torque.update(values)

class Guard:
    def __init__(self):self.confirmed=False
    def emit(self,row):pass
    def confirm_existing_goal(self,current,goal):
        if current!=goal:raise RuntimeError("원시 위치 불일치")
        self.confirmed=True
    @contextlib.contextmanager
    def permission(self,name,values):
        if not self.confirmed or name!="Torque_Enable":raise AssertionError("검증 전 모의 쓰기")
        yield

def api(bus):
    return SimpleNamespace(JOINTS=JOINTS,vector=vector,home_gate=home_gate,
        read_temperatures=lambda _:dict.fromkeys(JOINTS,30.),
        read_health=lambda _,**kw:dict.fromkeys(JOINTS,30.),
        read_motor_state=lambda _:(dict(bus.torque),dict.fromkeys(JOINTS,0)))

class OperatorTests(unittest.TestCase):
    def test_default_plan_no_runtime_import(self):
        with patch.dict("sys.modules",{"trial_runtime":None}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(first_trial.main([]),0)
    def test_live_missing_presence_refused_before_runtime(self):
        with self.assertRaises(Hold):first_trial.main(["--live","--execute"])
    def test_three_pass_required_current_hold_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"r.json";p.write_text(json.dumps(dict(planned=3,completed=3,passed=0,all_pass=False)))
            with self.assertRaises(Hold):first_trial.qualified(p)
    def test_no_automated_operator_input(self):
        with patch("sys.stdin.isatty",return_value=False),patch("builtins.input",side_effect=AssertionError("자동 입력 금지")):
            with self.assertRaises(Hold):confirm("ENABLE","시험")
    def test_live_prompt_monitors_without_motor_write(self):
        from unittest.mock import MagicMock
        terminal=MagicMock();terminal.isatty.return_value=True;terminal.readline.return_value="LIVE\n"
        calls=[]
        with patch("sys.stdin",terminal),patch("select.select",return_value=([terminal],[],[])),contextlib.redirect_stdout(io.StringIO()):
            confirm("LIVE","시험",monitor=lambda:calls.append("read_only_monitor"))
        self.assertEqual(calls,["read_only_monitor","read_only_monitor"])
    def test_live_prompt_sensor_fault_refuses(self):
        from unittest.mock import MagicMock
        terminal=MagicMock();terminal.isatty.return_value=True
        with patch("sys.stdin",terminal),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(Hold):
            confirm("LIVE","시험",monitor=lambda:(_ for _ in ()).throw(Hold("온도55")))
        terminal.readline.assert_not_called()
    def test_arm_requires_exact_goal_and_torque_off(self):
        for fault in ("goal","partial_on"):
            bus=Bus();guard=Guard()
            if fault=="goal":bus.feedback[JOINTS[0]]+=1
            else:bus.torque[JOINTS[0]]=1
            with self.subTest(fault=fault),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):
                arm.arm(bus,api(bus),HOME,guard)
            self.assertEqual(bus.writes,[])
    def test_arm_fixture_only_torque_packet_no_goal(self):
        bus=Bus();guard=Guard()
        with contextlib.redirect_stdout(io.StringIO()):result=arm.arm(bus,api(bus),HOME,guard)
        self.assertEqual(result["goal_writes"],0);self.assertEqual(bus.writes,[("Torque_Enable",dict.fromkeys(JOINTS,1))])
    def test_control_zero_is_recorded_but_active_target_drives_mock_gate(self):
        bus=Bus();bus.goal=dict.fromkeys(JOINTS,0);guard=Guard()
        with contextlib.redirect_stdout(io.StringIO()):result=arm.arm(bus,api(bus),HOME,guard)
        self.assertEqual(result["control_goal_42"],dict.fromkeys(JOINTS,0))
        self.assertEqual(result["target_feedback_67"],dict.fromkeys(JOINTS,1000))
        self.assertEqual([name for name,_ in bus.writes],["Torque_Enable"])
    def test_first_wrong_torque_stops_before_reading_other_joints(self):
        bus=Bus();bus.torque[JOINTS[0]]=1;seen=[];read=bus.read
        def wrapped(register,joint,**kw):seen.append(joint);return read(register,joint,**kw)
        bus.read=wrapped
        with self.assertRaises(RuntimeError):arm.fresh_torque(bus,api(bus),expected=0)
        self.assertEqual(seen,[JOINTS[0]])
        self.assertEqual(bus.writes,[])
    def test_late_off_after_mock_enable_never_sends_second_packet(self):
        bus=Bus();guard=Guard();state=api(bus);ticks=[]
        def health(_ ,**kw):
            if bus.writes:
                ticks.append(1)
                bus.torque[JOINTS[0]]=0
            return dict.fromkeys(JOINTS,30.)
        state.read_health=health
        with patch("arm_source.time.sleep",return_value=None),contextlib.redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):
            arm.arm(bus,state,HOME,guard)
        self.assertEqual(len(bus.writes),1)
        self.assertEqual(ticks,[1])
    def test_packet_guard_denies_goal_and_unconfirmed_on(self):
        guard=arm.WriteGuard("enable",lambda _:None)
        with self.assertRaises(arm.BlockedCommand):
            with guard.permission("Torque_Enable",dict.fromkeys(JOINTS,1)):pass
        with self.assertRaises(arm.BlockedCommand):
            with guard.permission("Goal_Position",dict.fromkeys(JOINTS,1000)):pass
    def test_arm_reports_actual_moving_to_controller(self):
        bus=Bus();read=bus.read
        bus.read=lambda register,joint,**kw:1 if register=="Moving" else read(register,joint,**kw)
        adapter=ArmingAdapter({"home":HOME},lambda _:None)
        adapter.robot=SimpleNamespace(bus=bus);adapter.api=api(bus)
        with contextlib.redirect_stdout(io.StringIO()):state=adapter.read_state()
        self.assertTrue(state.moving);self.assertEqual(bus.writes,[])

if __name__=="__main__":unittest.main()
