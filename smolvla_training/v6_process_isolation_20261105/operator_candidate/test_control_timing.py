import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import profile_control_timing as profile
import reviewed_start_stop as reviewed
import hold_continuation as held
import trial_runtime
from first_trial_core import Hold,JOINTS,CAMERAS
from test_hold_continuation import fixture
from test_reviewed_action_stop import add_action_review_fixture


class FakeIO:
    def __init__(self):self.now=1.;self.snapshots=0;self.cameras=0
    def clock(self):return self.now
    def sleep(self,n):self.now+=n
    def snapshot(self):
        self.now+=.01;self.snapshots+=1
        return dict(state=[0.]*6,torque=dict.fromkeys(JOINTS,1),modes=dict.fromkeys(JOINTS,0),temperatures=dict.fromkeys(JOINTS,30.),loads=dict.fromkeys(JOINTS,0),statuses=dict.fromkeys(JOINTS,0))
    def capture_frames(self):
        self.now+=.015;self.cameras+=1
        return {n:dict(host_received=self.now,shape=[480,640,3],dtype="uint8",sha256=n) for n in CAMERAS}
    def check_records(self):self.now+=.003
    def send_target(self,*args):raise AssertionError("계측에서 목표 전송 금지")


class Tests(unittest.TestCase):
    def test_plan_no_runtime_or_subprocess(self):
        with patch.dict("sys.modules",{"trial_runtime":None}),patch("subprocess.run",side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(profile.main([]),0)
    def test_ten_read_cycles_record_independent_sections_without_writes(self):
        hw=FakeIO();rows=profile.measure(hw,clock=hw.clock,sleep=hw.sleep)
        self.assertEqual(len(rows),10);self.assertEqual(hw.snapshots,10);self.assertEqual(hw.cameras,2)
        for row in rows:
            self.assertAlmostEqual(row["snapshot_seconds"],.01);self.assertAlmostEqual(row["records_seconds"],.003)
            self.assertIsNone(row["goal_send_seconds"])
        self.assertAlmostEqual(rows[0]["read_only_cycle_seconds"],.028)
    def test_hot_sensor_refuses_without_any_goal(self):
        hw=FakeIO();original=hw.snapshot
        def hot():
            r=original();r["temperatures"][JOINTS[0]]=50.;return r
        hw.snapshot=hot
        with self.assertRaises(Hold):profile.measure(hw,clock=hw.clock,sleep=hw.sleep)
    def test_profile_receipt_cannot_enable_goal_permission(self):
        receipt=SimpleNamespace(start_review=SimpleNamespace(profile_only=True))
        with patch.object(trial_runtime,"dependencies",side_effect=AssertionError),self.assertRaises(Hold):trial_runtime.DeviceIO({},expected_torque=1,allow_goal=True,continuation=receipt).connect()
    def test_success_evidence_needs_new_read_only_confirmation_and_is_once_only(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home);rpins=add_action_review_fixture(home,root,pins)
            local=Path(__file__).resolve().parents[2]/"first_live_success_20261108/field_run"
            original=local if (local/"RESULT.json").exists() else Path.home()/reviewed.SUCCESS_FOLDER
            success=home/reviewed.SUCCESS_FOLDER;success.mkdir();(success/"RESULT.json").write_bytes((original/"RESULT.json").read_bytes())
            prior=json.loads((original/"LIVE_OPERATOR_REVIEW.json").read_text(encoding="utf-8"));prior["field_stop_sha256"]=pins["STOP_SHA"]
            claim=root/"start_review_usage"/(reviewed.ACTION_POST_SHA+".json");claim.write_text(json.dumps(prior),encoding="utf-8");rpins["SUCCESS_CLAIM_SHA"]=held.sha(claim)
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                review=reviewed.ReviewedStartStop(root,home=home,profile_success=True)
                receipt=held.HoldContinuation(root,log,home=home,start_review=review);receipt.bind_context(context)
                tokens=[];receipt.confirm_operator(lambda t,m:tokens.append(t));out=home/"run";out.mkdir();receipt.claim(out);receipt.require_claimed(context)
                self.assertEqual(tokens,["READ_TIMING"]);self.assertTrue(review.profile_only)
                self.assertEqual(json.loads((out/"LIVE_OPERATOR_REVIEW.json").read_text())["scope"],"READ_ONLY_CONTROL_TIMING")
                with self.assertRaises(Hold):held.HoldContinuation(root,log,home=home,start_review=review)


if __name__=="__main__":unittest.main()
