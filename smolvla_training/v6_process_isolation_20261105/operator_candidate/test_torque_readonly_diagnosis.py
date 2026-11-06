import ast
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import torque_readonly_diagnosis as diagnosis
from reply_integrity import ReplyFault


class Tests(unittest.TestCase):
    def fake(self, fault=None):
        calls = []
        replies = SimpleNamespace(history=[])
        replies.begin = lambda register: None
        class Bus:
            def sync_read(self, register, *, motors, normalize, num_retry):
                calls.append((register, motors))
                assert len(motors) == 1 and normalize is False and num_retry == 0
                value = fault(register, motors[0]) if fault else (0 if register == "Torque_Enable" else 30)
                replies.history.append(dict(raw_hex="합성", expected_id=1, status=0))
                return {motors[0]: value}
        return Bus(), replies, calls

    def test_normal_two_rounds_no_movement_methods(self):
        bus, replies, calls = self.fake()
        emitted = []
        with patch.object(diagnosis.time, "sleep"):
            result = diagnosis.probe(bus, replies, emitted.append)
        self.assertEqual(len(calls), 24)
        self.assertEqual(len(emitted), 24)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(value == 0 for row in result for value in row["torque"].values()))

    def test_first_nonzero_torque_stops_before_other_motor_or_temperature(self):
        bus, replies, calls = self.fake(lambda *args: 1)
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.probe(bus, replies, lambda row: None)
        self.assertEqual(error.exception.code, "NONZERO_TORQUE_REPLY")
        self.assertEqual(calls, [("Torque_Enable", [diagnosis.JOINTS[0]])])

    def test_45_degree_temperature_stops_without_next_motor(self):
        bus, replies, calls = self.fake(lambda register, joint: 0 if register == "Torque_Enable" else 45)
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.probe(bus, replies, lambda row: None)
        self.assertEqual(error.exception.code, "TEMPERATURE_STOP")
        self.assertEqual(len(calls), 7)

    def test_response_fault_is_kept_without_retry(self):
        raw = dict(raw_hex="ffff010200fc", received_bytes=6, expected_bytes=1)
        def fault(*args):raise ReplyFault("응답 길이 불일치", raw)
        bus, replies, calls = self.fake(fault)
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.probe(bus, replies, lambda row: None)
        self.assertEqual(error.exception.observed["response"], raw)
        self.assertEqual(len(calls), 1)

    def test_float_or_boolean_register_value_refused(self):
        for value in (0.0, False):
            with self.subTest(value=value):
                bus, replies, calls = self.fake(lambda *args: value)
                with self.assertRaises(diagnosis.DiagnosisStop) as error:
                    diagnosis.probe(bus, replies, lambda row: None)
                self.assertEqual(error.exception.code, "DECODE_MISMATCH")
                self.assertEqual(len(calls), 1)

    def test_default_only_checks_files_no_subprocess_or_device(self):
        with patch.object(diagnosis, "check_files", return_value=Path("calibration.json")), \
             patch.object(diagnosis, "service_gate", side_effect=AssertionError("장치 상태 조회")), \
             patch.object(diagnosis, "port_owners", side_effect=AssertionError("포트 조회")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnosis.main([]), 0)
        self.assertIn("READONLY_PLAN_READY", output.getvalue())

    def test_execute_requires_all_operator_flags_before_preflight(self):
        with patch.object(diagnosis, "check_files", return_value=Path("calibration.json")), \
             patch.object(diagnosis, "service_gate", side_effect=AssertionError("거부 이전 조회")), \
             self.assertRaisesRegex(RuntimeError, "현장 입회"):
            diagnosis.main(["--execute"])

    def test_no_confirmation_no_output_folder_or_hardware(self):
        with patch.object(diagnosis, "check_files", return_value=Path("calibration.json")), \
             patch.object(diagnosis, "service_gate"), patch.object(diagnosis, "port_owners"), \
             patch("builtins.input", return_value="NO"), patch.object(Path, "mkdir", side_effect=AssertionError("파일 생성")), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "사용자 확인"):
            diagnosis.main(["--execute", "--operator-present", "--supported", "--stop-ready"])

    def test_static_no_motor_write_calibration_or_robot_connect(self):
        tree = ast.parse(Path(diagnosis.__file__).read_text(encoding="utf-8"))
        denied = {"send_action", "sync_write", "write", "enable_torque", "disable_torque", "configure", "calibrate"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):continue
            # stream.write는 보고서 파일에만 사용한다.
            if node.func.attr == "write":
                self.assertEqual(ast.unparse(node.func.value), "stream")
                continue
            self.assertNotIn(node.func.attr, denied)
            if node.func.attr == "connect":
                self.assertEqual(ast.unparse(node.func.value), "bus")
                self.assertTrue(any(k.arg == "handshake" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords))
            if node.func.attr == "disconnect":
                self.assertTrue(any(k.arg == "disable_torque" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords))

    def test_port_query_accepts_only_empty_or_expected_owner(self):
        cases = [(1, "", "", set()), (0, "123", diagnosis.PORT + ":", {123})]
        for code, stdout, stderr, expected in cases:
            with self.subTest(code=code), patch.object(Path, "exists", return_value=True), \
                 patch.object(diagnosis.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)):
                diagnosis.port_owners(expected)

    def test_port_query_unknown_errors_or_other_owner_fail_closed(self):
        cases = [(1, "", "sudo: password required"), (0, "123", "unknown warning"),
                 (0, "999", diagnosis.PORT + ":")]
        for code, stdout, stderr in cases:
            with self.subTest(stderr=stderr), patch.object(Path, "exists", return_value=True), \
                 patch.object(diagnosis.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)), self.assertRaises(RuntimeError):
                diagnosis.port_owners(set())


if __name__ == "__main__":unittest.main()
