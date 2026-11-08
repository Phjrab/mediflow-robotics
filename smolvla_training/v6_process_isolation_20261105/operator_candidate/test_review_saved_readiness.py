import json
from pathlib import Path
import tempfile
import unittest
from review_saved_readiness import JOINTS, review


class Tests(unittest.TestCase):
    def write_run(self,root,name,*,feedback=False,bad_checksum=False,status="READONLY_POSITION_GOAL_REVIEW",writes=0):
        folder=root/name;folder.mkdir()
        rows=[];events=[]
        for motor,joint in enumerate(JOINTS,1):
            row=dict(joint=joint,present_raw=1000,goal_raw=0,current=0,home=0,home_delta=0)
            values={"Torque_Enable":(0,1),"Present_Temperature":(30,1),"Operating_Mode":(0,1),
                    "Present_Position":(1000,2),"Goal_Position":(0,2)}
            if feedback:
                row["goal_feedback_raw"]=1000;values["Goal_Feedback_67"]=(1000,2)
            for register,(value,length) in values.items():
                payload=value.to_bytes(length,"little")
                raw=bytes((255,255,motor,length+2,0))+payload
                raw+=bytes(((~sum(raw[2:]))&255,))
                if bad_checksum:raw=raw[:-1]+bytes((raw[-1]^1,))
                events.append(dict(reply=dict(register=register,expected_id=motor,expected_bytes=length,
                                              raw_hex=raw.hex(),communication=0)))
            rows.append(row)
        data=dict(status=status,position_goal_review=dict(joints=rows,home_within_5_degrees=True),
                  motor_register_writes=writes,port_closed=True,cleanup_errors=[],field_stop_preserved=True)
        (folder/"RESULT.json").write_text(json.dumps(data),encoding="utf-8")
        (folder/"responses.jsonl").write_text("\n".join(json.dumps(row) for row in events),encoding="utf-8")

    def test_missing_feedback_requests_read_only_without_live_authorization(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1")
            report=review(root,root/"FIELD_STOP.json")
            self.assertIn("GOAL_FEEDBACK_67_NOT_MEASURED",report["blockers"])
            self.assertIn("--inspect-goal-feedback",report["next_operator_read_command"])
            self.assertFalse(report["live_authorized"])

    def test_documented_feedback_matches_but_control_zero_never_authorizes_rearm(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1",feedback=True)
            report=review(root,root/"FIELD_STOP.json")
            self.assertIn("TORQUE_ENABLE_TRANSITION_UNVERIFIED",report["blockers"])
            self.assertIn("DOCUMENTED_TARGET67_MATCHES_PRESENT56",report["findings"])
            self.assertTrue(report["current_target_readback_verified"])
            self.assertFalse(report["rearm_authorized"])
            self.assertTrue(report["feedback_matches_current"])
            self.assertFalse(report["control_goal_matches_current"])
            self.assertFalse(report["live_authorized"])

    def test_latest_failed_run_is_not_replaced_by_older_pass(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1",feedback=True);self.write_run(root,"2",status="HOLD")
            report=review(root,root/"FIELD_STOP.json")
            self.assertEqual(report["latest_run"],"2")
            self.assertIn("LATEST_DIAGNOSIS_HOLD",report["blockers"])

    def test_claimed_pass_with_bad_raw_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1",feedback=True,bad_checksum=True)
            report=review(root,root/"FIELD_STOP.json")
            self.assertIn("RAW_REPLY_EVIDENCE_INVALID",report["blockers"])
            self.assertFalse(report["current_target_readback_verified"])
            self.assertFalse(report["live_authorized"])

    def test_documented_feedback_match_preserves_existing_stop(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1",feedback=True)
            stop=root/"FIELD_STOP.json";original=b'{"status":"HOLD"}\n';stop.write_bytes(original)
            report=review(root,stop)
            self.assertIn("FIELD_STOP_REVIEW_REQUIRED",report["blockers"])
            self.assertEqual(stop.read_bytes(),original)
            self.assertFalse(report["live_authorized"])
            self.assertFalse(report["rearm_authorized"])

    def test_invalid_feedback_bytes_cannot_become_verified_target(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);self.write_run(root,"1",feedback=True)
            path=root/"1/responses.jsonl"
            rows=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows=[row for row in rows if row["reply"]["register"]!="Goal_Feedback_67"]
            path.write_text("\n".join(json.dumps(row) for row in rows),encoding="utf-8")
            report=review(root,root/"FIELD_STOP.json")
            self.assertIn("FEEDBACK_RAW_REPLY_MISMATCH",report["blockers"])
            self.assertFalse(report["current_target_readback_verified"])
            self.assertFalse(report["rearm_authorized"])

    def test_unexpected_write_or_no_result_never_authorizes_live(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            self.assertIn("NO_SAVED_RESULT",review(root,root/"FIELD_STOP.json")["blockers"])
            self.write_run(root,"1",feedback=True,writes=1)
            report=review(root,root/"FIELD_STOP.json")
            self.assertIn("UNEXPECTED_RECORDED_WRITE",report["blockers"])
            self.assertFalse(report["live_authorized"])


if __name__=="__main__":unittest.main()
