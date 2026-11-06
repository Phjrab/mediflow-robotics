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

    def positions(self, fault=None):
        calls = []
        replies = SimpleNamespace(history=[], begin=lambda name: None)
        class Bus:
            motors = {j: SimpleNamespace(id=i) for i, j in enumerate(diagnosis.JOINTS, 1)}
            calibration = {j: SimpleNamespace(range_min=500, range_max=2000) for j in diagnosis.JOINTS}
            def _normalize(self, values):return {motor_id: 10 if motor_id == 6 else 0 for motor_id in values}
            def sync_read(self, name, *, motors, normalize, num_retry):
                calls.append((name, motors[0]))
                assert normalize is False and num_retry == 0
                value = {"Torque_Enable": 0, "Present_Temperature": 30, "Operating_Mode": 0,
                         "Present_Position": 1000, "Goal_Position": 0}[name]
                if fault is not None:value = fault(name, motors[0], value)
                replies.history.append(dict(register=name))
                return {motors[0]: value}
        return Bus(), replies, calls, dict(state=[0] * 5 + [10])

    def test_goal_mismatch_is_reported_without_rewriting_it(self):
        bus, replies, calls, home = self.positions()
        result = diagnosis.position_goal_review(bus, replies, lambda row: None, home)
        self.assertEqual(len(calls), 30)
        self.assertFalse(result["goal_exact_match"])
        self.assertTrue(result["home_within_5_degrees"])
        self.assertFalse(result["live_authorized"])
        self.assertTrue(all(row["goal_raw"] == 0 and not row["goal_in_calibration"] for row in result["joints"]))

    def test_position_inspection_torque_on_stops_before_next_read(self):
        bus, replies, calls, home = self.positions(lambda name, joint, value: 1 if name == "Torque_Enable" else value)
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.position_goal_review(bus, replies, lambda row: None, home)
        self.assertEqual(error.exception.code, "NONZERO_TORQUE_REPLY")
        self.assertEqual(len(calls), 1)

    def test_wrong_mode_stops_before_position_read(self):
        bus, replies, calls, home = self.positions(lambda name, joint, value: 1 if name == "Operating_Mode" else value)
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.position_goal_review(bus, replies, lambda row: None, home)
        self.assertEqual(error.exception.code, "MODE_STOP")
        self.assertEqual(len(calls), 3)

    def test_matching_goals_do_not_authorize_live(self):
        bus, replies, calls, home = self.positions(lambda name, joint, value: 1000 if name == "Goal_Position" else value)
        result = diagnosis.position_goal_review(bus, replies, lambda row: None, home)
        self.assertTrue(result["goal_exact_match"])
        self.assertFalse(result["live_authorized"])

    def test_feedback67_compared_separately_without_authorizing_live(self):
        bus, replies, calls, home = self.positions()
        raw_calls = []
        def raw_read(addr, length, motor_ids, **kwargs):
            raw_calls.append((addr, length, motor_ids))
            self.assertEqual(kwargs["num_retry"], 0)
            self.assertTrue(kwargs["raise_on_error"])
            return {motor_ids[0]: 1000}, 0
        bus._sync_read = raw_read
        result = diagnosis.position_goal_review(bus, replies, lambda row: None, home, inspect_feedback=True)
        self.assertFalse(result["goal_exact_match"])
        self.assertTrue(result["feedback_exact_match"])
        self.assertFalse(result["live_authorized"])
        self.assertEqual(len(raw_calls), 6)
        self.assertTrue(all(addr == 67 and length == 2 and len(ids) == 1 for addr, length, ids in raw_calls))
        self.assertEqual(len(calls), 36)

    def test_feedback_error_stops_before_following_torque_or_motor(self):
        bus, replies, calls, home = self.positions()
        def raw_read(*args, **kwargs):raise ReplyFault("합성 피드백 오류", {"raw_hex": "", "register": "Goal_Feedback_67"})
        bus._sync_read = raw_read
        with self.assertRaises(diagnosis.DiagnosisStop) as error:
            diagnosis.position_goal_review(bus, replies, lambda row: None, home, inspect_feedback=True)
        self.assertEqual(error.exception.code, "RESPONSE_REJECTED")
        self.assertEqual(len(calls), 5)

    def test_feedback_option_alone_rejected_before_any_file_or_device(self):
        with patch.object(diagnosis, "check_files", side_effect=AssertionError("파일 접근")), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            diagnosis.main(["--inspect-goal-feedback"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":unittest.main()
