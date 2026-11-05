import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from first_trial_dependencies import prepare
prepare()
from live_safety_runtime import PacketPermission
from initialize_current_goal import JOINTS,exact_packet,goal_permission,main,InitializationAdapter

class Port:
    def __init__(self):self.sent=[]
    def writePort(self,data):self.sent.append(bytes(data));return len(data)

class GoalWriteTests(unittest.TestCase):
    def permission(self):
        port=Port();permission=goal_permission(PacketPermission)(port,allow_goal=True).install()
        return port,permission
    def test_default_no_hardware_import(self):
        with patch.dict(sys.modules,{"trial_runtime":None}),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main([]),0)
    def test_missing_operator_flags_no_hardware(self):
        with self.assertRaises(RuntimeError):main(["--execute"])
    def test_exact_one_goal_packet_only(self):
        port,permission=self.permission();current=dict.fromkeys(JOINTS,1000)
        try:
            permission.authorize(current)
            with permission.goal_once():port.writePort(exact_packet(current))
            self.assertEqual(permission.goal_packets,1);self.assertEqual(len(port.sent),1)
            with self.assertRaises(RuntimeError):port.writePort(exact_packet(current))
            self.assertEqual(len(port.sent),1)
        finally:permission.restore()
    def test_different_target_never_transmitted(self):
        port,permission=self.permission()
        try:
            permission.authorize(dict.fromkeys(JOINTS,1000))
            with self.assertRaises(RuntimeError):
                with permission.goal_once():port.writePort(exact_packet(dict.fromkeys(JOINTS,1001)))
            self.assertEqual(port.sent,[])
        finally:permission.restore()
    def test_torque_packet_never_transmitted(self):
        port,permission=self.permission()
        try:
            head=bytes((255,255,254,16,131,40,1))+b''.join(bytes((i,1)) for i in range(1,7))
            packet=head+bytes(((~sum(head[2:]))&255,))
            with self.assertRaises(RuntimeError):port.writePort(packet)
            self.assertEqual(port.sent,[])
        finally:permission.restore()
    def test_expired_current_snapshot_never_transmitted(self):
        port,permission=self.permission();current=dict.fromkeys(JOINTS,1000)
        try:
            with patch("initialize_current_goal.time.monotonic",return_value=1):permission.authorize(current)
            with patch("initialize_current_goal.time.monotonic",return_value=2),self.assertRaises(RuntimeError):
                with permission.goal_once():port.writePort(exact_packet(current))
            self.assertEqual(port.sent,[])
        finally:permission.restore()
    def test_actual_controller_routes_fixture_initialization(self):
        from controller_bridge import load_controller
        from first_trial_core import FirstMotionPlan
        for unexpected_on in (False,True):
            port=Port()
            class MockIO:
                def __init__(self):
                    self.permission=PacketPermission(port,allow_goal=False).install();self.written=False;self.closed=False;self.reads=[]
                    owner=self
                    class Bus:
                        port_handler=port
                        calibration={j:SimpleNamespace(range_min=100,range_max=3000) for j in JOINTS}
                        motors={j:SimpleNamespace(id=i) for i,j in enumerate(JOINTS,1)}
                        def sync_write(self,name,values,**kwargs):
                            if name!="Goal_Position" or kwargs!=dict(normalize=False,num_retry=0):raise AssertionError("미허가 쓰기")
                            port.writePort(exact_packet(values));owner.written=True
                    self.robot=SimpleNamespace(bus=Bus())
                def connect(self):pass
                def snapshot(self):return dict(state=[0.]*6,moving=False)
                def joint_bounds(self):return [(-180.,180.)]*5+[(0.,100.)]
                def raw(self,name):
                    self.reads.append(name)
                    if name=="Present_Position":return dict.fromkeys(JOINTS,1000)
                    if name=="Goal_Position":return dict.fromkeys(JOINTS,1000 if self.written else 0)
                    if name=="Torque_Enable":return dict.fromkeys(JOINTS,1 if self.written and unexpected_on else 0)
                    raise AssertionError(name)
                def health_snapshot(self):pass
                def check_records(self):pass
                def close(self):self.closed=True;self.permission.seal();self.permission.restore()
            hardware=MockIO();context={"home":dict(joint_names=list(JOINTS),state=[0.]*6,tolerance_deg=5.)}
            adapter=InitializationAdapter(hardware,context);adapter.physical=False
            controller=load_controller(mock_posix=True)(adapter)
            if unexpected_on:
                with self.assertRaisesRegex(RuntimeError,"토크ON"):controller.run(FirstMotionPlan(False,stages=("initialize_current_goal",)))
                self.assertEqual(hardware.reads[-1],"Torque_Enable")
            else:
                controller.run(FirstMotionPlan(False,stages=("initialize_current_goal",)))
                self.assertEqual(adapter.result["status"],"GOAL_INITIALIZED_TORQUE_OFF")
            self.assertTrue(hardware.closed);self.assertEqual(len(port.sent),1)
    def test_field_stop_blocks_live_before_runtime_import(self):
        import first_trial,tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/"FIELD_STOP.json").write_text('{}')
            with patch.object(first_trial,"__file__",str(Path(d)/"first_trial.py")),patch.dict(sys.modules,{"trial_runtime":None}),self.assertRaisesRegex(RuntimeError,"현장 중단 기록"):
                first_trial.main(["--live","--execute","--operator-present","--supported","--stop-ready"])

if __name__=="__main__":unittest.main()
