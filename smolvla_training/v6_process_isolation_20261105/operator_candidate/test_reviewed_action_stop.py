import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import hold_continuation as held
import reviewed_start_stop as reviewed
from first_trial_core import Hold
from test_hold_continuation import fixture
from test_reviewed_start_stop import add_review_fixture


def action_files():
    local=Path(__file__).resolve().parents[2]/"action_bounds_hold_20261108/field_run"
    if (local/"RESULT.json").exists():return dict(result=local/"RESULT.json",stop=local/"POST_START_REVIEW_STOP.json",claim=local/"prior_start_usage.json")
    home=Path.home();root=home/held.PROJECT_FOLDER/"operator_candidate"
    return dict(result=home/reviewed.ACTION_RESULT_FOLDER/"RESULT.json",stop=root/reviewed.NEXT_STOP,claim=root/"start_review_usage"/(reviewed.POST_SHA+".json"))


def add_action_review_fixture(home,root,pins):
    rpins=add_review_fixture(home,root,pins);files=action_files()
    folder=home/reviewed.ACTION_RESULT_FOLDER;folder.mkdir()
    shutil.copy2(files["result"],folder/"RESULT.json");shutil.copy2(files["stop"],root/reviewed.NEXT_STOP)
    prior=json.loads(files["claim"].read_text(encoding="utf-8"));prior["field_stop_sha256"]=pins["STOP_SHA"]
    path=root/"start_review_usage"/(reviewed.POST_SHA+".json");path.parent.mkdir();path.write_text(json.dumps(prior),encoding="utf-8")
    rpins["ACTION_CLAIM_SHA"]=held.sha(path)
    return rpins


class Tests(unittest.TestCase):
    def setup_case(self,home):
        root,log,context,pins,_=fixture(home);rpins=add_action_review_fixture(home,root,pins)
        return root,log,context,pins,rpins

    def test_action_review_needs_new_token_and_preserves_both_prior_stops(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            old={p.name:p.read_bytes() for p in (root/held.NEW_STOP,root/reviewed.NEXT_STOP)}
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                review=reviewed.ReviewedStartStop(root,home=home,review_action=True)
                receipt=held.HoldContinuation(root,log,home=home,start_review=review);receipt.bind_context(context)
                with self.assertRaises(Hold):receipt.require_claimed(context)
                tokens=[];receipt.confirm_operator(lambda token,message:tokens.append(token));out=home/"run";out.mkdir();receipt.claim(out);receipt.require_claimed(context)
                self.assertEqual(tokens,["REARM_ACTION"])
                with self.assertRaises(Hold):held.HoldContinuation(root,log,home=home,start_review=review)
            self.assertEqual(old,{p.name:p.read_bytes() for p in (root/held.NEW_STOP,root/reviewed.NEXT_STOP)})

    def test_prior_start_entry_still_refuses_its_new_stop(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins),self.assertRaises(Hold):reviewed.ReviewedStartStop(root,home=home)

    def test_changed_current_evidence_refuses(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            p=home/reviewed.ACTION_RESULT_FOLDER/"RESULT.json";p.write_bytes(p.read_bytes()+b" ")
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins),self.assertRaises(Hold):reviewed.ReviewedStartStop(root,home=home,review_action=True)

    def test_new_action_failure_adds_a_third_stop_and_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,rpins=self.setup_case(home)
            before=(root/reviewed.NEXT_STOP).read_bytes()
            with patch.multiple(held,**pins),patch.multiple(reviewed,**rpins):
                review=reviewed.ReviewedStartStop(root,home=home,review_action=True)
                result=held.latch_new_stop(root,dict(error={"reason":"fixture"}),start_review=review)
                self.assertEqual(result["name"],reviewed.ACTION_NEXT_STOP)
                self.assertEqual((root/reviewed.NEXT_STOP).read_bytes(),before)
                with self.assertRaises(Hold):review.check()


if __name__=="__main__":unittest.main()
