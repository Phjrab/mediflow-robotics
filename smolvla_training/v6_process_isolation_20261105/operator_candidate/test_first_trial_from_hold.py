import contextlib
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import first_trial_from_hold as entry
import hold_continuation as held
from first_trial_core import JOINTS,CAMERAS,Hold
from controller_bridge import load_controller
from test_hold_continuation import fixture


class FakeIO:
    physical=True
    instances=[]
    temp=30.;camera_fault=False
    def __init__(self,context,*,expected_torque,allow_goal=False,policy=None,continuation=None):
        if expected_torque!=1:raise AssertionError("이미 ON인 흐름에서 OFF를 요구함")
        self.context=context;self.expected_torque=expected_torque;self.allow_goal=allow_goal;self.policy=policy;self.continuation=continuation
        self.state=list(context["home"]["state"]);self.closed=False;self.frame=0;self.authorized=False;self.cleanup_errors=[];self.last_fault=None
        serial=SimpleNamespace(is_open=False);serial.close=lambda:setattr(serial,"is_open",False)
        self.robot=SimpleNamespace(bus=SimpleNamespace(port_handler=SimpleNamespace(ser=serial,is_open=False)))
        self.permission=SimpleNamespace(goal_packets=0,seal=lambda:None,restore=lambda:None)
        self.targets=[];FakeIO.instances.append(self)
    def connect(self):self.continuation.require_claimed(self.context);self.robot.bus.port_handler.ser.is_open=True
    def close(self):self.closed=True;self.robot.bus.port_handler.ser.is_open=False
    def snapshot(self):return dict(self.health_snapshot(),state=list(self.state),moving=False)
    def health_snapshot(self):return dict(torque=dict.fromkeys(JOINTS,1),modes=dict.fromkeys(JOINTS,0),temperatures=dict.fromkeys(JOINTS,self.temp),loads=dict.fromkeys(JOINTS,0),statuses=dict.fromkeys(JOINTS,0))
    def inspect_goal(self):return dict(snapshot=self.snapshot(),arm_ready=True)
    def joint_bounds(self):return [(-180.,180.)]*5+[(0.,100.)] # 모의 범위이며 실기 보정에 쓰지 않음
    def connect_cameras(self):
        if self.camera_fault:raise Hold("fixture camera fault")
    def capture_frames(self):
        self.frame+=1
        return {name:dict(pixels=None,shape=[480,640,3],dtype="uint8",host_received=time.monotonic(),sha256=name+str(self.frame)) for name in CAMERAS}
    def predict(self,frames,state):return [[float(v)+.1 for v in state] for _ in range(50)]
    def check_records(self):self.continuation.require_claimed(self.context)
    def send_target(self,target):
        if not self.authorized:raise AssertionError("LIVE 확인 전 목표")
        self.continuation.require_claimed(self.context);self.permission.goal_packets+=1;self.targets.append(target);self.state=list(target)


class Tests(unittest.TestCase):
    def setUp(self):FakeIO.instances=[];FakeIO.temp=30.;FakeIO.camera_fault=False
    def run_case(self,home,*,reject_live=False,preflight_fault=False,review_start=False):
        from first_trial_dependencies import prepare
        dependency_root=prepare()
        root,log,context,pins,summary=fixture(home);out=home/"run"
        import reviewed_start_stop as reviewed
        review_pins={}
        if review_start:
            from test_reviewed_start_stop import add_review_fixture
            review_pins=add_review_fixture(home,root,pins)
        class Policy:
            def __init__(self,*args):self.closed=False
            def close(self):self.closed=True
        module=SimpleNamespace(static_context=lambda:context,DeviceIO=FakeIO,InspectionAdapter=__import__("trial_runtime").InspectionAdapter,PolicyProvider=Policy)
        tokens=[]
        def confirm(token,message,**kwargs):
            tokens.append(token)
            if reject_live and token=="LIVE":raise Hold("fixture LIVE rejected")
        def preflight():
            if preflight_fault:raise Hold("fixture preflight rejected")
        image=SimpleNamespace(fromarray=lambda pixels:SimpleNamespace(save=lambda path:Path(path).write_bytes(b"fixture image")))
        with patch.multiple(held,**pins),patch.multiple(reviewed,**review_pins) if review_pins else contextlib.nullcontext(),patch.object(entry,"STOP_SHA",pins["STOP_SHA"]),patch.object(Path,"home",return_value=home), \
             patch.dict("os.environ",{"V6_PREP_ROOT":str(dependency_root)}), \
             patch.object(entry,"__file__",str(root/"first_trial_from_hold.py")),patch.object(entry,"preflight",side_effect=preflight), \
             patch.object(entry,"load_controller",side_effect=lambda:load_controller(mock_posix=True)), \
             patch("sys.stdin.isatty",return_value=True),patch("operator_arm.confirm",side_effect=confirm), \
             patch("operator_arm.ArmingAdapter",side_effect=AssertionError("토크 변경 adapter 호출 금지")), \
             patch.dict(sys.modules,{"trial_runtime":module,"PIL":SimpleNamespace(Image=image),"runtime_cpu":SimpleNamespace(configure_main=lambda path:None)}), \
             contextlib.redirect_stdout(io.StringIO()):
            code=entry.main(["--live","--execute","--operator-present","--supported","--stop-ready","--hold-log",str(log),"--repeat-summary",str(summary),"--out",str(out)]+(["--review-start-hold"] if review_start else []))
        return code,root,out,tokens

    def test_default_plan_never_imports_runtime_or_gpu(self):
        with patch.dict(sys.modules,{"trial_runtime":None}),patch("subprocess.run",side_effect=AssertionError("계획에서 subprocess 금지")),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main([]),0)

    def test_live_missing_presence_refuses_before_import(self):
        with patch.dict(sys.modules,{"trial_runtime":None}),self.assertRaises(Hold):entry.main(["--live","--execute"])

    def test_check_files_is_nonconsuming_and_has_no_runtime_or_subprocess(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,summary=fixture(home)
            before={str(p.relative_to(home)):p.read_bytes() for p in home.rglob("*") if p.is_file()}
            with patch.multiple(held,**pins),patch.object(Path,"home",return_value=home),patch.object(entry,"__file__",str(root/"first_trial_from_hold.py")), \
                 patch.dict(sys.modules,{"trial_runtime":None}),patch("subprocess.run",side_effect=AssertionError("파일 검사에서 subprocess 금지")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(entry.main(["--check-files","--hold-log",str(log),"--repeat-summary",str(summary)]),0)
            self.assertEqual(before,{str(p.relative_to(home)):p.read_bytes() for p in home.rglob("*") if p.is_file()})

    def test_mock_complete_uses_original_controller_ten_goals_and_zero_torque_changes(self):
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name))
            data=json.loads((out/"RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(code,0,data.get("error"));self.assertEqual(tokens,["REARM_HOLD","LIVE"])
            self.assertEqual(data["motor_goal_packets"],10);self.assertEqual(data["torque_register_writes"],0)
            self.assertTrue(data["original_stop_preserved"]);self.assertFalse((root/held.NEW_STOP).exists())
            self.assertEqual(len(FakeIO.instances[1].targets),10);self.assertTrue(all(item.closed for item in FakeIO.instances))

    def test_live_not_confirmed_sends_zero_and_latches_new_stop(self):
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name),reject_live=True)
            self.assertEqual(code,2);self.assertTrue((root/held.NEW_STOP).is_file())
            self.assertTrue(all(not item.targets for item in FakeIO.instances))
            self.assertTrue(all(item.closed for item in FakeIO.instances))
            self.assertTrue((root/"FIELD_STOP.json").is_file())

    def test_preflight_failure_does_not_consume_hold_receipt_or_open_devices(self):
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name),preflight_fault=True)
            self.assertEqual(code,2);self.assertEqual(tokens,[]);self.assertEqual(FakeIO.instances,[])
            self.assertFalse((root/"hold_usage").exists());self.assertFalse((root/held.NEW_STOP).exists())

    def test_hot_start_refuses_goal_writes(self):
        FakeIO.temp=50.
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name))
            self.assertEqual(code,2);self.assertEqual(tokens,["REARM_HOLD"])
            self.assertEqual(len(FakeIO.instances),1);self.assertFalse(FakeIO.instances[0].targets)
            self.assertTrue((root/held.NEW_STOP).exists())

    def test_camera_fault_refuses_goals_and_preserves_stop(self):
        FakeIO.camera_fault=True
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name))
            self.assertEqual(code,2);self.assertTrue(all(not item.targets for item in FakeIO.instances))
            self.assertTrue((root/held.NEW_STOP).exists());self.assertTrue((root/"FIELD_STOP.json").is_file())

    def test_reviewed_start_requires_new_tokens_and_uses_no_torque_writes(self):
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name),review_start=True)
            data=json.loads((out/"RESULT.json").read_text())
            self.assertEqual(code,0,data.get("error"));self.assertEqual(tokens,["REARM_START","LIVE"])
            self.assertEqual(data["motor_goal_packets"],10);self.assertEqual(data["torque_register_writes"],0)
            self.assertTrue((root/held.NEW_STOP).exists());self.assertFalse((root/"POST_START_REVIEW_STOP.json").exists())

    def test_reviewed_start_failure_preserves_prior_stop_and_adds_next_stop(self):
        with tempfile.TemporaryDirectory() as name:
            code,root,out,tokens=self.run_case(Path(name),review_start=True,reject_live=True)
            self.assertEqual(code,2);self.assertEqual(tokens,["REARM_START","LIVE"])
            self.assertTrue((root/held.NEW_STOP).exists());self.assertTrue((root/"POST_START_REVIEW_STOP.json").exists())
            self.assertTrue(all(not item.targets for item in FakeIO.instances))


if __name__=="__main__":unittest.main()
