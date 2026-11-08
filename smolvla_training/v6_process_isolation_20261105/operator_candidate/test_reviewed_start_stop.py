from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import hold_continuation as held
import reviewed_start_stop as reviewed
import trial_runtime
from first_trial_core import Hold,JOINTS
from test_hold_continuation import fixture


def incident_files():
    local=Path(__file__).resolve().parents[2]/"start_goal_hold_20261108/field_run"
    if (local/"RESULT.json").exists():
        return dict(result=local/"RESULT.json",stop=local/"POST_HOLD_STOP.json",claim=local/(held.HOLD_SHA+".json"))
    home=Path.home();root=home/held.PROJECT_FOLDER/"operator_candidate"
    return dict(result=home/reviewed.RESULT_FOLDER/"RESULT.json",stop=root/held.NEW_STOP,claim=root/"hold_usage"/(held.HOLD_SHA+".json"))


def add_review_fixture(home,root,pins):
    files=incident_files()
    folder=home/reviewed.RESULT_FOLDER;folder.mkdir()
    shutil.copy2(files["result"],folder/"RESULT.json")
    shutil.copy2(files["stop"],root/held.NEW_STOP)
    previous=json.loads(files["claim"].read_text(encoding="utf-8"));previous["field_stop_sha256"]=pins["STOP_SHA"]
    marker=root/"hold_usage"/(pins["HOLD_SHA"]+".json");marker.parent.mkdir()
    marker.write_text(json.dumps(previous),encoding="utf-8")
    return dict(CLAIM_SHA=held.sha(marker))


class Tests(unittest.TestCase):
    def setup_case(self,home):
        root,log,context,pins,summary=fixture(home)
        review_pins=add_review_fixture(home,root,pins)
        return root,log,context,pins,review_pins

    def test_review_does_not_authorize_or_change_old_stops(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            before={p.name:p.read_bytes() for p in (root/"FIELD_STOP.json",root/held.NEW_STOP)}
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                review=reviewed.ReviewedStartStop(root,home=home)
                receipt=held.HoldContinuation(root,log,home=home,start_review=review)
                with self.assertRaises(Hold):receipt.require_claimed(context)
                receipt.bind_context(context);tokens=[];receipt.confirm_operator(lambda token,message:tokens.append(token))
                out=home/"run";out.mkdir();receipt.claim(out);receipt.require_claimed(context)
                self.assertEqual(tokens,["REARM_START"])
                with self.assertRaises(Hold):held.HoldContinuation(root,log,home=home,start_review=review)
            self.assertEqual(before,{p.name:p.read_bytes() for p in (root/"FIELD_STOP.json",root/held.NEW_STOP)})

    def test_old_entry_still_refuses_reviewed_stop(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            with patch.multiple(held,**pins),self.assertRaises(Hold):held.HoldContinuation(root,log,home=home)

    def test_changed_review_evidence_refused(self):
        for item in ("result","stop","claim"):
            with self.subTest(item=item),tempfile.TemporaryDirectory() as name:
                home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
                paths=dict(result=home/reviewed.RESULT_FOLDER/"RESULT.json",stop=root/held.NEW_STOP,claim=root/"hold_usage"/(pins["HOLD_SHA"]+".json"))
                paths[item].write_bytes(paths[item].read_bytes()+b" ")
                with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins),self.assertRaises(Hold):reviewed.ReviewedStartStop(root,home=home)

    def test_new_failure_is_additional_stop_and_blocks_reuse(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home);old=(root/held.NEW_STOP).read_bytes()
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                review=reviewed.ReviewedStartStop(root,home=home)
                result=held.latch_new_stop(root,dict(error={"reason":"fixture"},motor_goal_packets=0),start_review=review)
                self.assertTrue(result["created"])
                self.assertEqual((root/held.NEW_STOP).read_bytes(),old)
                with self.assertRaises(Hold):review.check()

    def test_wrong_scope_or_motor_writes_cannot_be_rearmed(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            p=home/reviewed.RESULT_FOLDER/"RESULT.json";data=json.loads(p.read_text(encoding="utf-8"));data["motor_goal_packets"]=1;p.write_text(json.dumps(data),encoding="utf-8")
            rpins["RESULT_SHA"]=held.sha(p)
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins),self.assertRaises(Hold):reviewed.ReviewedStartStop(root,home=home)

    def test_explicit_operator_token_still_required(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                receipt=held.HoldContinuation(root,log,home=home,start_review=reviewed.ReviewedStartStop(root,home=home));receipt.bind_context(context)
                with self.assertRaises(Hold):receipt.confirm_operator(lambda *args:(_ for _ in ()).throw(Hold("취소")))
                with self.assertRaises(Hold):receipt.claim(home)

    def inspect(self,extra_raw=0):
        data=json.loads(incident_files()["result"].read_text(encoding="utf-8"))["inspection"]
        present=data["present_raw"];target=dict(data["target_feedback_raw"]);target["shoulder_pan"]-=extra_raw
        state=data["snapshot"]["state"]
        def normalize(values):
            return {i:state[i-1]+(raw-present[JOINTS[i-1]])*(360/4095 if i<6 else .06) for i,raw in values.items()}
        bus=SimpleNamespace(motors={j:SimpleNamespace(id=i+1) for i,j in enumerate(JOINTS)},packet_handler=object(),_normalize=normalize)
        home=dict(joint_names=list(JOINTS),state=list(state),tolerance_deg=5.)
        io=trial_runtime.DeviceIO(dict(home=home),expected_torque=1)
        io.robot=SimpleNamespace(bus=bus);io.permission=SimpleNamespace(goal_packets=0)
        io.snapshot=lambda:deepcopy(data["snapshot"])
        io.raw=lambda name:dict(present) if name=="Present_Position" else dict(data["goal_raw"])
        io.joint_bounds=lambda:data["joint_bounds"]
        with patch("individual_reply_guard.StrictIndividualReplies"),patch("individual_reply_guard.read_target_feedback",return_value=target):return io.inspect_goal()

    def test_actual_inspection_one_count_passes_and_preserves_exact_flag(self):
        result=self.inspect()
        self.assertFalse(result["active_target_exact_match"]);self.assertTrue(result["arm_ready"])
        self.assertEqual(result["register_writes"],0)

    def test_actual_inspection_two_counts_refuses(self):
        self.assertFalse(self.inspect(extra_raw=1)["arm_ready"])


if __name__=="__main__":unittest.main()
