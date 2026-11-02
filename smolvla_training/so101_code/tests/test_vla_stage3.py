import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vla_fixture import fixture, mutate
from vla_data import Dataset, tree_hash, official_worker, write_new, JOINTS, sha
from vla_audit_dataset import audit, main as audit_main, units_evidence
from vla_build_derived import build, exclusion_plan
from vla_review_episode import append_review, history, latest_reviews, render
from vla_split_sessions import split, validate_split, session_evidence
from vla_sessions import new_run, read_sessions, commit_with_session, validate_sessions_path
from vla_record import RecordError, ROOT
from test_vla_record import FakeDataset

OFFICIAL=os.environ.get("VLA_OFFICIAL_SOURCE")


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.root=fixture(self.base/"dataset"); self.ds=Dataset(self.root)

    def codes(self, report):
        return {r["code"] for e in report["episodes"] for r in e["reasons"]}

    def write_ledger(self,rows,path):
        from vla_record import new_counter
        counter_path=self.base/"synthetic-counter.json"
        counter=new_counter(self.root,"local/test"); counter["actual_repo_id"]="local/test"
        for row in rows:
            row["counter_path"]=str(counter_path)
            counter["episodes"].append(dict(index=row["episode_index"],combo=row["combo"],task=row["task"],fingerprint=row["episode_fingerprint"]))
            counter["counts"][row["combo"]]+=1
        counter["revision"]=len(rows)
        counter_path.write_text(json.dumps(counter)); path.write_text("".join(json.dumps(r)+"\n" for r in rows))

    def test_full_decode_and_source_unchanged(self):
        before=tree_hash(self.root); result=audit(self.root)
        self.assertEqual(result["summary"],{"OK":0,"WARN":3,"FAIL":0})
        self.assertIn("UNITS_UNVERIFIED",self.codes(result)); self.assertEqual(before,tree_hash(self.root))

    def test_timestamp_duplicate(self):
        mutate(self.root,"rows",lambda x:x[1].update(timestamp=0))
        self.assertIn("TIMESTAMP_NONMONOTONIC",self.codes(audit(self.root)))

    def test_timestamp_gap(self):
        mutate(self.root,"rows",lambda x:x[2].update(timestamp=.08))
        self.assertIn("TIMESTAMP_INTERVAL",self.codes(audit(self.root)))

    def test_missing_frame(self):
        mutate(self.root,"rows",lambda x:x.pop(1))
        self.assertIn("FRAME_MISSING_OR_INDEX",self.codes(audit(self.root)))

    def test_nan_action(self):
        mutate(self.root,"rows",lambda x:x[1]["action"].__setitem__(0,float("nan")))
        self.assertIn("STATE_ACTION_NONFINITE_DIM",self.codes(audit(self.root)))

    def test_jump_is_candidate_not_collision_proof(self):
        mutate(self.root,"rows",lambda x:x[1]["action"].__setitem__(0,100))
        self.assertIn("JUMP_CANDIDATE",self.codes(audit(self.root)))

    def test_wrong_task(self):
        mutate(self.root,"rows",lambda x:x[1].update(task_index=999))
        self.assertIn("TASK_MISSING_BLANK_MULTIPLE",self.codes(audit(self.root)))

    def test_video_frame_count(self):
        mutate(self.root,"meta",lambda x:x[0].update({"videos/observation.images.top/to_timestamp":.1}))
        self.assertIn("VIDEO_FRAME_COUNT",self.codes(audit(self.root)))

    def test_video_missing(self):
        (self.root/"videos/observation.images.top/chunk-000/file-000.mp4").unlink()
        self.assertIn("VIDEO_MISSING_OR_UNREADABLE",self.codes(audit(self.root)))

    def test_metadata_only_marks_unchecked(self):
        self.assertIn("VIDEO_FRAME_COUNT_NOT_RUN",self.codes(audit(self.root,video_mode="metadata")))

    def test_camera_and_joint_order(self):
        path=self.root/"meta/info.json"; info=json.loads(path.read_text())
        del info["features"]["observation.images.top"]
        info["features"]["action"]["names"].reverse(); path.write_text(json.dumps(info))
        codes=self.codes(audit(self.root)); self.assertIn("CAMERA_KEYS_MISSING",codes); self.assertIn("JOINT_DIM_NAMES_ORDER",codes)

    def test_unproven_units_never_apply_bounds(self):
        e=self.base/"units.json"; e.write_text("{}")
        self.assertEqual(units_evidence(self.ds,e)["status"],"UNKNOWN")

    def test_proven_matching_limits_and_gripper_heuristic(self):
        mutate(self.root,"rows",lambda x:x[1]["action"].__setitem__(5,80))
        ds=Dataset(self.root); reference=self.base/"synthetic_unit_evidence.txt"
        reference.write_text("synthetic degree declaration\n")
        labels=["Rotation","Pitch","Elbow","Wrist_Pitch","Wrist_Roll"]
        bounds=self.base/"bounds.json"; write_new(bounds,dict(source_sha256="b"*64,normalization_mode="DEGREES",resolution_steps=4096,arm_ranges_raw={k:[2000,2001] for k in labels}))
        evidence=self.base/"units.json"; write_new(evidence,dict(dataset_binding=ds.binding,joint_names=JOINTS,arm_unit="degree",
            gripper_unit="percent",coordinate_system="lerobot-degrees-midpoint-4095",bounds_order=labels,calibration_sha256="b"*64,
            evidence=[dict(file=str(reference),line=1,contains="degree declaration",sha256=sha(reference))]))
        codes=self.codes(audit(self.root,units=evidence,bounds=bounds))
        self.assertIn("CALIBRATION_LIMIT_EXCEEDED",codes)

    def test_session_fingerprint_mismatch_rejected(self):
        ledger=self.base/"sessions.jsonl"; meta=self.ds.metas[0]
        row=dict(schema_version=1,episode_index=0,dataset_binding=dict(root=str(self.root),repo_id="local/test"),
                 **new_run("session1"),combo="A:red",task=meta["tasks"][0],camera_config_sha256="a"*64,episode_fingerprint="b"*64)
        self.write_ledger([row],ledger)
        self.assertIn("SESSION_FINGERPRINT_MISMATCH",self.codes(audit(self.root,sessions=ledger)))
        with self.assertRaises(RecordError): session_evidence(self.ds,ledger)
        row["episode_fingerprint"]=self.ds.fingerprint(meta); self.write_ledger([row],ledger)
        self.assertEqual(session_evidence(self.ds,ledger)[0]["session_id"],"session1")

    def test_fatal_audit_writes_both_reports(self):
        out=self.base/"badreport"
        self.assertEqual(audit_main(["--root",str(self.base/"missing"),"--out",str(out)]),2)
        self.assertTrue((out/"DATASET_AUDIT.json").exists()); self.assertTrue((out/"DATA_READINESS.md").exists())

    def test_review_append_latest_and_binding(self):
        p=self.base/"review.csv"
        append_review(self.ds,1,p,"fail","bad demonstration","synthetic reviewer"); before=p.read_bytes()
        append_review(self.ds,1,p,"ok","corrected review","synthetic reviewer")
        self.assertTrue(p.read_bytes().startswith(before)); self.assertEqual(len(history(p,self.ds.binding)),2)
        self.assertEqual(latest_reviews(p,self.ds.binding)[1]["verdict"],"ok")
        self.assertFalse(history(p,dict(self.ds.binding,metadata_sha256="bad")))

    def test_contact_sheet_and_plot(self):
        out=render(self.ds,1,self.base/"review",5,True)
        self.assertTrue((out/"contact_sheet.png").is_file()); self.assertTrue((out/"state_action.png").is_file())

    def test_review_inside_source_rejected(self):
        with self.assertRaises(RecordError): append_review(self.ds,0,self.root/"reviews.csv","ok","test","tester")

    def test_dry_run_no_writes(self):
        before=tree_hash(self.base); result=build(self.root,audit(self.root),None,self.base/"derived")
        self.assertEqual(result["status"],"DRY_RUN"); self.assertEqual(before,tree_hash(self.base))

    def test_existing_output_and_source_output_rejected(self):
        for out in (self.root,self.base,self.root/"derived"):
            with self.assertRaises((RecordError,FileExistsError)): build(self.root,audit(self.root),None,out)

    def test_review_hold_excluded_and_manual_ok_cannot_override_fail(self):
        p=self.base/"review.csv"; append_review(self.ds,0,p,"ok","review","tester")
        append_review(self.ds,1,p,"hold","unclear","tester")
        report=audit(self.root); report["episodes"][0]["reasons"].append({"code":"BAD","severity":"FAIL"})
        self.assertEqual([r["episode_index"] for r in exclusion_plan(self.ds,report,p)["excluded_episodes"]],[0,1])

    def test_wrong_audit_binding_rejected(self):
        report=audit(self.root); report["dataset_binding"]["metadata_sha256"]="bad"
        with self.assertRaises(RecordError): exclusion_plan(self.ds,report)

    def test_split_unknown_not_guessed(self):
        result=split(self.ds,session_evidence(self.ds)); self.assertEqual(result["unknown_count"],3)
        self.assertFalse(result["train_episodes"]); self.assertFalse(result["eval_episodes"])

    def test_split_grouped_deterministic_and_no_leakage(self):
        evidence={i:dict(session_id="s"+str(i//2),combo="A:red",source_root=str(self.root),source_episode_index=i) for i in range(3)}
        result=split(self.ds,evidence,.5,9); self.assertEqual(result,split(self.ds,evidence,.5,9))
        self.assertEqual(0 in result["train_episodes"],1 in result["train_episodes"])
        self.assertFalse(set(result["train_sessions"])&set(result["eval_sessions"]))

    def test_split_validator_rejects_leakage(self):
        result=split(self.ds,{0:dict(session_id="s",combo="A:red")}); result["eval_sessions"]=["s"]
        with self.assertRaises(RecordError): validate_split(result)

    @unittest.skipUnless(OFFICIAL,"set VLA_OFFICIAL_SOURCE for official v0.6.0 integration")
    def test_official_reload_and_derived_checksum(self):
        before=tree_hash(self.root); report=official_worker(self.root,OFFICIAL)
        self.assertEqual(report["status"],"PASS",report)
        reviews=self.base/"reviews.csv"; append_review(self.ds,1,reviews,"fail","synthetic exclusion","tester")
        result=build(self.root,audit(self.root),reviews,self.base/"derived",True,OFFICIAL)
        self.assertEqual(result["status"],"PASS"); self.assertEqual(before,tree_hash(self.root))
        dst=Dataset(self.base/"derived"); self.assertEqual(dst.info["total_frames"],12)
        self.assertEqual([m["episode_index"] for m in dst.metas],[0,1])
        lineage=json.loads((dst.root/"provenance.json").read_text())["episodes"]
        self.assertEqual([e["source_episode_index"] for e in lineage],[0,2])
        self.assertEqual(split(dst,session_evidence(dst))["unknown_count"],2)

    @unittest.skipUnless(OFFICIAL,"set VLA_OFFICIAL_SOURCE for official v0.6.0 integration")
    def test_derived_preserves_explicit_sessions(self):
        ledger=self.base/"sessions.jsonl"; rows=[]
        for meta in self.ds.metas:
            i=meta["episode_index"]
            rows.append(dict(schema_version=1,episode_index=i,dataset_binding=dict(root=str(self.root),repo_id="local/test"),
                **new_run("session"+str(i)),combo="A:red" if i%2==0 else "B:blue",task=meta["tasks"][0],
                camera_config_sha256="a"*64,episode_fingerprint=self.ds.fingerprint(meta)))
        self.write_ledger(rows,ledger)
        reviews=self.base/"review.csv"; append_review(self.ds,1,reviews,"hold","synthetic","tester")
        build(self.root,audit(self.root),reviews,self.base/"derived",True,OFFICIAL,sessions=ledger)
        dst=Dataset(self.base/"derived"); evidence=session_evidence(dst,ledger); result=split(dst,evidence,.5)
        self.assertEqual(result["unknown_count"],0); self.assertEqual(len(result["eval_episodes"]),1)
        self.assertEqual({e["source_episode_index"] for e in evidence.values()},{0,2})
        mutate(dst.root,"rows",lambda x:x[0]["action"].__setitem__(0,99))
        with self.assertRaises(RecordError): session_evidence(Dataset(dst.root),ledger)


class SessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.root=self.base/"dataset"; self.fixture=FakeDataset(self.root)
        self.before=self.fixture.snapshot(); self.counter=self.base/"counter.json"; self.ledger=self.base/"sessions.jsonl"
        from vla_record import load_tasks
        self.task=load_tasks(ROOT/"configs/vla_tasks.yaml")["combinations"]["A:red"]["ko"][0]
        self.fixture.append(self.task)

    def commit(self):
        return commit_with_session(self.counter,self.root,"local/test",self.before,0,"A:red",self.task,
            sessions_path=self.ledger,run=new_run("synthetic-session"),camera_config_sha256="a"*64,
            exit_code=0,completed=True,interrupted=False,snapshot_reader=self.fixture.snapshot,
            identity_reader=lambda _:"local/test")

    def test_commit_and_duplicate_rejected(self):
        self.commit(); before=self.counter.read_bytes(); ledger=self.ledger.read_bytes()
        with self.assertRaises(RecordError): self.commit()
        self.assertEqual(before,self.counter.read_bytes()); self.assertEqual(ledger,self.ledger.read_bytes())
        self.assertEqual(read_sessions(self.ledger)[0]["session_id"],"synthetic-session")

    def test_sidecar_failure_counter_unchanged(self):
        with patch("vla_sessions.atomic_append_bytes",side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.commit()
        self.assertFalse(self.counter.exists()); self.assertFalse(self.ledger.exists())

    def test_second_append_same_session(self):
        self.commit(); previous=self.ledger.read_bytes(); before=self.fixture.snapshot(); self.fixture.append(self.task)
        result=commit_with_session(self.counter,self.root,"local/test",before,1,"A:red",self.task,
            sessions_path=self.ledger,run=new_run("synthetic-session"),camera_config_sha256="a"*64,
            exit_code=0,completed=True,interrupted=False,snapshot_reader=self.fixture.snapshot,
            identity_reader=lambda _:"local/test")
        self.assertEqual(result["revision"],2); self.assertTrue(self.ledger.read_bytes().startswith(previous))

    def test_counter_without_ledger_not_backfilled(self):
        self.commit(); self.ledger.unlink()
        with self.assertRaises(RecordError): self.commit()
        self.assertFalse(self.ledger.exists())

    def test_counter_failure_after_sidecar_fail_closed_retry(self):
        real=os.replace
        def replace(source,dest):
            if Path(dest)==self.counter: raise OSError("counter write failure")
            return real(source,dest)
        with patch("os.replace",side_effect=replace):
            with self.assertRaises(OSError): self.commit()
        self.assertFalse(self.counter.exists()); self.assertEqual(len(read_sessions(self.ledger,require_committed=False)),1)
        with self.assertRaises(RecordError): read_sessions(self.ledger)
        with self.assertRaises(RecordError): self.commit()
        self.assertFalse(self.counter.exists())

    def test_partial_line_rejected(self):
        self.ledger.write_bytes(b'{"incomplete":')
        with self.assertRaises(RecordError): self.commit()

    def test_unique_run_and_no_implicit_historical_session(self):
        a,b=new_run(),new_run(); self.assertNotEqual(a["run_id"],b["run_id"]); self.assertNotEqual(a["session_id"],b["session_id"])

    def test_overlapping_paths_rejected(self):
        for p in (self.root/"session.jsonl",self.counter):
            with self.assertRaises(RecordError): validate_sessions_path(p,self.root,self.counter)


if __name__=="__main__": unittest.main()
