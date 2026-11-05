import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import dry_session as ds
import observation_contract as observation
from checkpoint_amp import infer_with_saved_amp
from live_safety_runtime import Hold

ROOT=Path(__file__).resolve().parent

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.code=self.home/"code";self.code.mkdir()
        self.stop_root=self.home/"nov_live_prep_20261104";self.stop_root.mkdir()
        data={"reason":"토크ON 관측; 이후 serial 작업 중단","observed":{"joint":"shoulder_pan","torque":{"shoulder_pan":1}}}
        self.stop=self.stop_root/ds.STOP_NAME;self.stop.write_text(json.dumps(data),encoding="utf-8")
        self.original=self.stop.read_bytes()
        self.addCleanup(patch.stopall)
        patch.object(ds.Path,"home",return_value=self.home).start()
        patch.object(ds,"__file__",str(self.code/"dry_session.py")).start()
        patch.object(ds,"STOP_HASH",ds.sha(self.stop)).start()
        files={}
        for name in ("unit_evidence","home_state","calibration","camera_config","model_config","preprocessor","postprocessor"):
            p=self.home/(name+".json");p.write_text("{}")
            files[name]={"path":p.name,"sha256":ds.sha(p)}
        probe=self.code/"probe.json";probe.write_text("{}")
        now=time.time()
        self.data=dict(schema_version=1,scope="READ_ONLY_DRY_RUN",live_permitted=False,motor_writes_allowed=False,
            issued_unix=now,expires_unix=now+300,stop_inventory=ds.stop_inventory(),model_sha256=ds.MODEL_HASH,
            probe_file=probe.name,probe_sha256=ds.sha(probe),files=files,checkpoint="model",run_id="synthetic-fixture",
            cameras={"end_effector":"wrist","ceiling_vertical":"top","ceiling_oblique":"side"})
        self.path=self.code/"receipt.json";self.write()
    def write(self):self.path.write_text(json.dumps(self.data),encoding="utf-8")
    def args(self):
        return types.SimpleNamespace(live=False,checkpoint=self.home/"model",unit_evidence=self.home/"unit_evidence.json",
          home_state=self.home/"home_state.json",end_effector_camera=None,ceiling_vertical_camera=None,ceiling_oblique_camera=None)
    def test_one_read_only_session_keeps_original_record(self):
        session=ds.DrySession(self.path);args=self.args();session.begin(args);session.check()
        self.assertEqual(args.end_effector_camera,"wrist")
        self.assertEqual(self.stop.read_bytes(),self.original)
        with self.assertRaises(Hold):ds.DrySession(self.path)
    def test_live_always_refused(self):
        for live in (True,1,"false"):
            with self.assertRaises(Hold):ds.DrySession(self.path,live=live)
    def test_wrong_scope_or_write_permission_refused(self):
        for key,value in (("scope","LIVE"),("motor_writes_allowed",True),("live_permitted",True)):
            old=self.data[key];self.data[key]=value;self.write()
            with self.assertRaises(Hold):ds.DrySession(self.path)
            self.data[key]=old
    def test_expired_or_future_receipt_refused(self):
        now=time.time()
        for issued,expires in ((now-400,now-100),(now+10,now+100),(now,now+301)):
            self.data.update(issued_unix=issued,expires_unix=expires);self.write()
            with self.assertRaises(Hold):ds.DrySession(self.path)
    def test_new_stop_record_or_modified_archived_record_refused(self):
        session=ds.DrySession(self.path)
        active=self.stop_root/"SERIAL_STOP.json";active.write_text("{}")
        with self.assertRaises(Hold):session.check()
        active.unlink()  # 테스트 임시 fixture만 제거. 실제 기록은 수정하지 않는다.
        self.stop.write_text("{}")
        with self.assertRaises(Hold):session.check()
    def test_settings_change_refused(self):
        session=ds.DrySession(self.path)
        (self.home/"camera_config.json").write_text('{"changed":true}')
        with self.assertRaises(Hold):session.check()
    def test_receipt_change_refused(self):
        session=ds.DrySession(self.path)
        self.data["expires_unix"]+=1;self.write()
        with self.assertRaises(Hold):session.check()
    def test_camera_and_checkpoint_override_refused(self):
        session=ds.DrySession(self.path)
        args=self.args();args.ceiling_oblique_camera="other"
        with self.assertRaises(Hold):session.begin(args)
        args=self.args();args.checkpoint=self.home/"other"
        with self.assertRaises(Hold):session.begin(args)
    def test_consumed_record_removal_is_not_rearm(self):
        session=ds.DrySession(self.path);session.begin(self.args())
        self.path.with_suffix(".json.consumed.json").write_text("{}")
        with self.assertRaises(Hold):session.check()

class ProbeGateTests(unittest.TestCase):
    def valid(self):
        return dict(status="READ_ONLY_STATUS_COMPLETE_NOT_LIVE_READY",diagnostic_only=True,end_unix=time.time(),
          motor_calibration_matches=True,home_body_within_5deg=True,stop_record_unchanged=True,lelab_state="inactive",
          owners_before={"free":True},owners_after={"free":True},register_writes=0,goal_packets=0,blocked_packets=0,cleanup_errors=[],
          torque=dict.fromkeys(ds.api.JOINTS,0),modes=dict.fromkeys(ds.api.JOINTS,0),
          temperatures=dict.fromkeys(ds.api.JOINTS,35),positions=dict.fromkeys(ds.api.JOINTS,0))
    def test_fresh_all_off_probe_accepted(self):
        self.assertEqual(ds.safe_probe(self.valid(),time.time()),[0]*6)
    def test_on_hot_old_unverified_probe_refused(self):
        for name,value in (("torque",1),("temperatures",45),("modes",1)):
            p=self.valid();p[name][ds.api.JOINTS[0]]=value
            with self.assertRaises(Hold):ds.safe_probe(p,time.time())
        p=self.valid();p["end_unix"]-=61
        with self.assertRaises(Hold):ds.safe_probe(p,time.time())
        p=self.valid();p["home_body_within_5deg"]="true"
        with self.assertRaises(Hold):ds.safe_probe(p,time.time())

class DryVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location("dry_variant",ROOT/"so101_first_action_test_safe_v6_dry.py")
        cls.runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.runner)
    def test_live_refused_before_hardware_import(self):
        before=set(sys.modules)
        with patch.object(sys,"argv",["v6","--live","--dry-session","fake.json","--robot-id","so-101","--task","Put medicine bottle A into the blue basket"]):
            with self.assertRaises(Hold):self.runner.main()
        self.assertFalse({"torch","cv2","serial","lerobot"}&(set(sys.modules)-before))
    def test_torque_on_stops_after_first_read(self):
        calls=[]
        bus=types.SimpleNamespace(read=lambda *a,**kw:(calls.append(a) or 1))
        with self.assertRaises(Hold):self.runner.read_motor_state(bus)
        self.assertEqual(len(calls),1)
    def test_45_temperature_stops_after_first_read(self):
        calls=[]
        bus=types.SimpleNamespace(read=lambda *a,**kw:(calls.append(a) or 45))
        with self.assertRaises(Hold):self.runner.read_health(bus)
        self.assertEqual(len(calls),1)
    def test_goal_permission_is_unconditionally_disabled(self):
        source=(ROOT/"so101_first_action_test_safe_v6_dry.py").read_text(encoding="utf-8")
        self.assertIn("allow_goal=False",source)
        self.assertIn("timeout=50.0 / args.fps",source)
        self.assertIn("home_gate(state,home)",source)

class CameraContractTests(unittest.TestCase):
    def contract(self):
        return dict(schema_version=1,rename_source="checkpoint.policy_preprocessor.json",width=640,height=480,channels=3,fps=30,warmup_seconds=3,color_mode="rgb",
          cameras={"end_effector":"/dev/v4l/by-id/usb-camera-video-index0","ceiling_vertical":"/dev/video8","ceiling_oblique":"/dev/v4l/by-path/platform-usb:1.3-video-index0"},
          camera_identity={"ceiling_oblique":{"vendor":"8086","product":"0b07","serial":"236223023645","interface":"03","fourcc":"YUYV"}})
    def load(self,value):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"config.json";p.write_text(json.dumps(value))
            return observation.load_contract(p)
    def test_identified_rgb_path_accepted(self):
        self.assertEqual(self.load(self.contract())["camera_identity"]["ceiling_oblique"]["interface"],"03")
    def test_depth_interface_wrong_serial_and_missing_proof_refused(self):
        value=self.contract();value["cameras"]["ceiling_oblique"]="/dev/v4l/by-path/platform-usb:1.0-video-index0"
        with self.assertRaises(ValueError):self.load(value)
        value=self.contract();value["camera_identity"]["ceiling_oblique"]["serial"]="other"
        with self.assertRaises(ValueError):self.load(value)
        value=self.contract();del value["camera_identity"]
        with self.assertRaises(ValueError):self.load(value)

class AmpSettingTests(unittest.TestCase):
    def torch(self,events):
        class Context:
            def __enter__(self):events.append("enter")
            def __exit__(self,*args):events.append("exit")
        def autocast(**kw):events.append(kw);return Context()
        return types.SimpleNamespace(autocast=autocast)
    def test_saved_true_applied_around_prediction(self):
        events=[];config={"use_amp":True}
        self.assertEqual(infer_with_saved_amp(lambda:(events.append("predict") or 5),self.torch(events),config),5)
        self.assertEqual(events,[{"device_type":"cuda","enabled":True},"enter","predict","exit"])
        self.assertEqual(config,{"use_amp":True})
    def test_saved_false_is_not_automatically_promoted(self):
        events=[];infer_with_saved_amp(lambda:None,self.torch(events),{"use_amp":False})
        self.assertEqual(events[0]["enabled"],False)
    def test_missing_or_nonboolean_setting_refused(self):
        for config in ({},{"use_amp":"true"},{"use_amp":1}):
            events=[]
            with self.assertRaises(ValueError):infer_with_saved_amp(lambda:None,self.torch(events),config)
            self.assertEqual(events,[])

if __name__=="__main__":unittest.main()
