import contextlib
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
from run_receipts import finish_resources, save_run, verify_target_receipt


class Tests(unittest.TestCase):
    def test_close_error_still_closes_serial_and_policy(self):
        calls = []
        class Serial:
            is_open = True
            def close(self):calls.append("serial_close");self.is_open = False
        class Resource:
            robot = SimpleNamespace(bus=SimpleNamespace(port_handler=SimpleNamespace(ser=Serial(),is_open=True)))
            permission = SimpleNamespace(seal=lambda: calls.append("seal"),restore=lambda: calls.append("restore"))
            cleanup_errors = []
            def close(self):raise RuntimeError("합성 정리 오류")
        result = finish_resources(("io",Resource()),("policy",SimpleNamespace(close=lambda: calls.append("policy_close"))))
        self.assertTrue(result["ports_closed"]["io"])
        self.assertEqual(calls,["seal","serial_close","restore","policy_close"])
        self.assertEqual(result["errors"][0]["component"],"io_close")

    def test_existing_stop_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);out=root/"out";out.mkdir();stop=root/"FIELD_STOP.json"
            stop.write_bytes(b'{"original":true}\n');before=hashlib.sha256(stop.read_bytes()).hexdigest()
            result=save_run(root,out,dict(status="HOLD",error={"reason":"새 실패"}),latch=True)
            self.assertEqual(hashlib.sha256(stop.read_bytes()).hexdigest(),before)
            self.assertTrue(result["stop_receipt"]["existing_preserved"])

    def test_new_live_hold_creates_latch_and_result(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);out=root/"out";out.mkdir()
            result=save_run(root,out,dict(status="HOLD",error={"observed":{"torque":{"joint":1}}}),latch=True)
            self.assertTrue(result["stop_receipt"]["created"])
            self.assertEqual(json.loads((root/"FIELD_STOP.json").read_text(encoding="utf-8"))["status"],"HOLD")
            self.assertEqual(json.loads((out/"RESULT.json").read_text(encoding="utf-8"))["error"]["observed"]["torque"]["joint"],1)

    def test_cleanup_failure_forces_hold_even_after_stage_complete(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            result=save_run(root,root,dict(status="FIRST_TRIAL_COMPLETE",cleanup_errors=["합성 오류"]),latch=True)
            self.assertEqual(result["status"],"HOLD")
            self.assertTrue((root/"FIELD_STOP.json").is_file())

    def test_nonfinite_observation_is_saved_without_invalid_json(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            result=save_run(root,root,dict(status="HOLD",observed=[float("nan"),float("inf")]))
            self.assertEqual(result["observed"],["nan","inf"])
            self.assertFalse((root/"FIELD_STOP.json").exists())

    def test_transmission_count_mismatch_is_not_complete(self):
        with self.assertRaisesRegex(RuntimeError,"송신 시도"):
            verify_target_receipt(SimpleNamespace(permission=SimpleNamespace(goal_packets=0)),[{}]*10,10)
        verify_target_receipt(SimpleNamespace(permission=SimpleNamespace(goal_packets=10)),[{}]*10,10)

    def test_static_context_failure_still_writes_result_without_device(self):
        import first_trial
        module=SimpleNamespace(static_context=lambda: (_ for _ in ()).throw(RuntimeError("합성 근거 오류")),
                               DeviceIO=None,InspectionAdapter=None,PolicyProvider=None,dependencies=None)
        with tempfile.TemporaryDirectory() as name:
            out=Path(name)/"output"
            with patch.dict(sys.modules,{"trial_runtime":module}),contextlib.redirect_stdout(io.StringIO()):
                code=first_trial.main(["--inspect","--out",str(out)])
            self.assertEqual(code,2)
            result=json.loads((out/"RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(result["error"]["reason"],"합성 근거 오류")
            self.assertEqual(result["motor_goal_packets"],0)

    def test_policy_close_is_idempotent_and_finalizes_after_worker_error(self):
        from trial_runtime import PolicyProvider
        calls=[]
        policy=PolicyProvider.__new__(PolicyProvider);policy.closed=False
        def close_worker():calls.append("worker");raise RuntimeError("합성 worker 오류")
        policy.worker=SimpleNamespace(close=close_worker)
        policy.policy=SimpleNamespace(close=lambda:calls.append("policy"))
        with self.assertRaises(RuntimeError):policy.close()
        policy.close()
        self.assertEqual(calls,["worker","policy"])

    def test_worker_creation_failure_closes_already_created_policy(self):
        import trial_runtime
        calls=[]
        fake_policy=SimpleNamespace(close=lambda:calls.append("policy_close"))
        factory=SimpleNamespace(from_pretrained=lambda *args,**kwargs:fake_policy)
        def failed_worker():raise RuntimeError("합성 worker 생성 오류")
        with patch.object(trial_runtime,"dependencies",return_value=None), \
             patch.dict(sys.modules,{"torch":SimpleNamespace(),"process_policy":SimpleNamespace(ProcessPolicy=factory),
                                     "persistent_policy_worker":SimpleNamespace(PersistentPolicyWorker=failed_worker)}), \
             self.assertRaisesRegex(RuntimeError,"worker 생성 오류"):
            trial_runtime.PolicyProvider({"checkpoint":"fixture"},"fixture task")
        self.assertEqual(calls,["policy_close"])

    def test_initialization_cleanup_failure_does_not_hide_original_error(self):
        import trial_runtime
        def failed_close():raise OSError("합성 정리 오류")
        factory=SimpleNamespace(from_pretrained=lambda *args,**kwargs:SimpleNamespace(close=failed_close))
        def failed_worker():raise RuntimeError("원래 worker 생성 오류")
        with patch.object(trial_runtime,"dependencies",return_value=None), \
             patch.dict(sys.modules,{"torch":SimpleNamespace(),"process_policy":SimpleNamespace(ProcessPolicy=factory),
                                     "persistent_policy_worker":SimpleNamespace(PersistentPolicyWorker=failed_worker)}), \
             self.assertRaisesRegex(RuntimeError,"원래 worker 생성 오류") as error:
            trial_runtime.PolicyProvider({"checkpoint":"fixture"},"fixture task")
        self.assertEqual(error.exception.initialization_cleanup_error,"OSError")


if __name__=="__main__":unittest.main()
