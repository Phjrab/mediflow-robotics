"""지연된 토크 불일치에서도 기존 응답만 기록하고 추가 읽기를 하지 않는지 확인."""
import json
from types import SimpleNamespace
import unittest
from first_trial_core import JOINTS,Hold
from trial_runtime import DeviceIO
from pathlib import Path
from unittest.mock import patch

class ObservationTests(unittest.TestCase):
    def make_io(self,torques):
        calls=[]
        class Bus:
            def sync_read(self,name,**kwargs):
                calls.append(name)
                if name=="Torque_Enable":return dict.fromkeys(JOINTS,torques.pop(0))
                if name=="Present_Temperature":return dict.fromkeys(JOINTS,45)
                raise AssertionError("고장 감지 후 추가 읽기: "+name)
        io=DeviceIO({},expected_torque=0);io.robot=SimpleNamespace(bus=Bus())
        return io,calls
    def test_late_on_keeps_first_off_and_fault_on_without_extra_reads(self):
        io,calls=self.make_io([0,1])
        io.diagnostic_phase="POST_GOAL_INITIAL_TORQUE"
        self.assertTrue(all(v==0 for v in io.raw("Torque_Enable").values()))
        io.diagnostic_phase="POST_GOAL_HEALTH_CHECK"
        with self.assertRaises(Hold) as caught:io.health_snapshot()
        fault=caught.exception.observed
        self.assertEqual(calls,["Torque_Enable","Torque_Enable"])
        self.assertEqual(fault["phase"],"POST_GOAL_HEALTH_CHECK")
        self.assertEqual(fault["torque"],dict.fromkeys(JOINTS,1))
        self.assertEqual(fault["recent_reads"][0]["values"],dict.fromkeys(JOINTS,0))
        json.dumps(fault,allow_nan=False)
    def test_hot_stops_after_existing_temperature_reply(self):
        io,calls=self.make_io([0])
        with self.assertRaises(Hold) as caught:io.health_snapshot()
        self.assertEqual(calls,["Torque_Enable","Present_Temperature"])
        self.assertEqual(caught.exception.observed["threshold"],45)
    def test_bad_numeric_reply_is_json_serializable(self):
        io,calls=self.make_io([float("nan")])
        with self.assertRaises(Hold) as caught:io.health_snapshot()
        self.assertEqual(calls,["Torque_Enable"])
        self.assertEqual(caught.exception.observed["values"][JOINTS[0]],"nan")
        json.dumps(caught.exception.observed,allow_nan=False)
    def test_read_error_keeps_register_without_second_request(self):
        io,calls=self.make_io([])
        def fail(name,**kwargs):calls.append(name);raise ConnectionError("합성 응답 오류")
        io.robot.bus.sync_read=fail
        with self.assertRaises(ConnectionError) as caught:io.raw("Torque_Enable")
        self.assertEqual(calls,["Torque_Enable"])
        self.assertEqual(caught.exception.observed["register"],"Torque_Enable")
        self.assertTrue(caught.exception.observed["read_failed"])
    def test_field_stop_prevents_any_connect_dependency_or_device(self):
        import tempfile,trial_runtime
        io=DeviceIO({})
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/"FIELD_STOP.json").write_text('{}')
            with patch.object(trial_runtime,"__file__",str(Path(folder)/"trial_runtime.py")),patch.object(trial_runtime,"dependencies",side_effect=AssertionError("장치 의존성 접근 금지")),self.assertRaises(Hold):io.connect()
    def test_status_fault_attaches_already_read_snapshot(self):
        io,calls=self.make_io([])
        def status_fault(name,**kwargs):
            calls.append(name)
            return dict.fromkeys(JOINTS,30 if name=="Present_Temperature" else 1 if name=="Status" else 0)
        io.robot.bus.sync_read=status_fault
        with self.assertRaises(Hold) as caught:io.health_snapshot()
        self.assertEqual(calls,["Torque_Enable","Present_Temperature","Operating_Mode","Present_Load","Status"])
        self.assertEqual(caught.exception.observed["snapshot"]["statuses"],dict.fromkeys(JOINTS,1))
    def test_raw_fault_preserves_response_bytes_without_more_requests(self):
        from reply_integrity import ReplyFault
        io,calls=self.make_io([])
        response=dict(raw_hex="ffff010200fc",expected_bytes=1,received_bytes=6,status=0)
        def fail(name,**kwargs):
            calls.append(name)
            raise ReplyFault("합성 ACK 길이 불일치",response)
        io.robot.bus.sync_read=fail
        with self.assertRaises(ReplyFault) as caught:io.raw("Torque_Enable")
        self.assertEqual(calls,["Torque_Enable"])
        self.assertEqual(caught.exception.observed["response"],response)

if __name__=="__main__":unittest.main()
