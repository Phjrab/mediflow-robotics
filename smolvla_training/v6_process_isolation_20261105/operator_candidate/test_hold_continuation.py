import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import hold_continuation as held
from first_trial_core import Hold,JOINTS


def fixture(home):
    root=home/held.PROJECT_FOLDER/"operator_candidate";root.mkdir(parents=True)
    folder=home/"torque_guard_fixture";folder.mkdir()
    choices=[Path(__file__).resolve().parents[2]/"torque_hold_pass_20261108/field_run",Path.home()/"torque_guard_20261108_155436"]
    originals=next(p for p in choices if (p/"events.jsonl").is_file() and (p/"HOLD_REVIEW.json").is_file())
    shutil.copy2(originals/"events.jsonl",folder/"events.jsonl")
    stop=root/"FIELD_STOP.json";stop.write_text('{"fixture_stop":true}',encoding="utf-8")
    review=json.loads((originals/"HOLD_REVIEW.json").read_text(encoding="utf-8"))
    review["stop_sha256"]=held.sha(stop)
    (folder/"HOLD_REVIEW.json").write_text(json.dumps(review),encoding="utf-8")
    rows=[json.loads(line) for line in (folder/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    result=next(row for row in rows if row.get("event")=="RESULT")
    state=[result["result"]["current"][j] for j in JOINTS]
    homefile=home/"home_state_so101.json";homefile.write_text(json.dumps(dict(joint_names=list(JOINTS),state=state,tolerance_deg=5.)))
    calibration=home/"calibration.json";calibration.write_text('{"fixture_calibration":true}')
    files={p.name:held.sha(p) for p in (homefile,calibration)}
    context=dict(homefile=homefile,calibration=calibration,home=json.loads(homefile.read_text()),files=files,mapping={},stop_inventory={})
    pins=dict(STOP_SHA=held.sha(stop),HOLD_SHA=held.sha(folder/"events.jsonl"),REVIEW_SHA=held.sha(folder/"HOLD_REVIEW.json"),CAL_SHA=held.sha(calibration),HOME_SHA=held.sha(homefile))
    summary=home/"repeat.json";summary.write_text(json.dumps(dict(planned=3,completed=3,passed=3,all_pass=True,cases=[dict(status="V6_FAST_DRY_COMPLETE",register_writes=0,post_checks=True,record_preserved=True)]*3)))
    return root,folder/"events.jsonl",context,pins,summary


class Tests(unittest.TestCase):
    def test_hold_receipt_alone_never_permits_device_access(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            with patch.multiple(held,**pins):
                receipt=held.HoldContinuation(root,log,home=home)
                with self.assertRaises(Hold):receipt.require_claimed(context)
                with self.assertRaises(Hold):receipt.claim(home)

    def test_manual_review_consumes_once_preserving_original(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home);before=(root/"FIELD_STOP.json").read_bytes()
            with patch.multiple(held,**pins):
                receipt=held.HoldContinuation(root,log,home=home);receipt.bind_context(context)
                tokens=[];receipt.confirm_operator(lambda token,message:tokens.append(token))
                out=home/"run";out.mkdir();receipt.claim(out);receipt.require_claimed(context)
                self.assertEqual(tokens,["REARM_HOLD"])
                with self.assertRaises(Hold):held.HoldContinuation(root,log,home=home)
            self.assertEqual((root/"FIELD_STOP.json").read_bytes(),before)

    def test_changed_stop_or_hold_log_refuses_before_claim(self):
        for target in ("FIELD_STOP.json","events.jsonl"):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as name:
                home=Path(name);root,log,context,pins,_=fixture(home)
                changed=root/target if target=="FIELD_STOP.json" else log
                changed.write_bytes(changed.read_bytes()+b" ")
                with patch.multiple(held,**pins),self.assertRaises(Hold):held.HoldContinuation(root,log,home=home)
                self.assertFalse((root/"hold_usage").exists())

    def test_new_stop_refuses_even_with_new_review(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            (root/held.NEW_STOP).write_text('{"status":"HOLD"}')
            with patch.multiple(held,**pins),self.assertRaises(Hold):held.HoldContinuation(root,log,home=home)

    def test_used_marker_or_context_tampering_refuses(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            with patch.multiple(held,**pins):
                receipt=held.HoldContinuation(root,log,home=home);receipt.bind_context(context);receipt.confirm_operator(lambda *args:None)
                out=home/"run";out.mkdir();receipt.claim(out)
                changed=dict(context,files={"tampered":"sha"})
                with self.assertRaises(Hold):receipt.require_claimed(changed)
                receipt.marker.write_text("{}")
                with self.assertRaises(Hold):receipt.require_claimed(context)

    def test_failed_operator_confirmation_does_not_consume(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            with patch.multiple(held,**pins):
                receipt=held.HoldContinuation(root,log,home=home);receipt.bind_context(context)
                with self.assertRaises(Hold):receipt.confirm_operator(lambda *args:(_ for _ in ()).throw(Hold("입력 불일치")))
                with self.assertRaises(Hold):receipt.claim(home)
            self.assertFalse((root/"hold_usage").exists())

    def test_new_stop_is_append_only_and_original_is_unchanged(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home);before=(root/"FIELD_STOP.json").read_bytes()
            first=held.latch_new_stop(root,dict(error={"reason":"fixture","observed":float("nan")},motor_goal_packets=1))
            new_before=(root/held.NEW_STOP).read_bytes()
            second=held.latch_new_stop(root,dict(error={"reason":"different"}))
            self.assertTrue(first["created"]);self.assertTrue(second["existing_preserved"])
            self.assertEqual((root/held.NEW_STOP).read_bytes(),new_before)
            self.assertEqual(json.loads(new_before)["error"]["observed"],"nan")
            self.assertEqual((root/"FIELD_STOP.json").read_bytes(),before)

    def test_copying_into_new_project_folder_cannot_reuse_proof(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            copied=home/"other_project/operator_candidate";copied.mkdir(parents=True)
            (copied/"FIELD_STOP.json").write_bytes((root/"FIELD_STOP.json").read_bytes())
            with patch.multiple(held,**pins),self.assertRaises(Hold):held.HoldContinuation(copied,log,home=home)


if __name__=="__main__":unittest.main()
