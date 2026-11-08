import contextlib
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import start_goal_agreement as review

HERE = Path(__file__).resolve().parent


class Tests(unittest.TestCase):
    def setUp(self):
        self.path = HERE / "field_run/RESULT.json"
        self.original = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = deepcopy(self.original["inspection"])

    def check(self):
        d = self.data
        return review.assess(d["present_raw"], d["target_feedback_raw"], d["snapshot"]["state"],
                             d["target_feedback_state"], d["joint_bounds"])

    def test_real_incident_is_one_count_and_never_live_permission(self):
        result = self.check()
        self.assertFalse(result["current_raw_exact"])
        self.assertTrue(result["proposed_start_check_passed"])
        self.assertEqual([r["delta_raw"] for r in result["rows"]], [-1, 1, 0, 0, 0, 0])
        self.assertFalse(result["live_authorized"])

    def test_exact_case_is_also_only_a_proposal(self):
        self.data["target_feedback_raw"] = dict(self.data["present_raw"])
        self.data["target_feedback_state"] = list(self.data["snapshot"]["state"])
        result = self.check()
        self.assertTrue(result["current_raw_exact"])
        self.assertFalse(result["live_authorized"])

    def test_two_raw_counts_refused_even_with_small_normalized_difference(self):
        self.data["target_feedback_raw"]["shoulder_pan"] -= 1
        self.assertFalse(self.check()["proposed_start_check_passed"])

    def test_normalized_difference_over_point_one_refused(self):
        self.data["target_feedback_state"][0] = self.data["snapshot"]["state"][0] - 0.11
        self.assertFalse(self.check()["proposed_start_check_passed"])

    def test_bounds_violation_refused(self):
        self.data["joint_bounds"][0] = [-5.9, 118]
        self.assertFalse(self.check()["proposed_start_check_passed"])

    def test_nan_inf_refused(self):
        for value in (float("nan"), float("inf")):
            self.data["target_feedback_state"][0] = value
            with self.assertRaises(ValueError):
                self.check()

    def test_dimension_refused(self):
        self.data["snapshot"]["state"].pop()
        with self.assertRaises(ValueError):
            self.check()

    def test_joint_names_refused(self):
        self.data["present_raw"]["unknown"] = self.data["present_raw"].pop("gripper")
        with self.assertRaises(ValueError):
            self.check()

    def test_boolean_raw_refused(self):
        self.data["present_raw"]["gripper"] = True
        with self.assertRaises(ValueError):
            self.check()

    def test_invalid_bounds_refused(self):
        self.data["joint_bounds"][0] = [1, -1]
        with self.assertRaises(ValueError):
            self.check()

    def test_gripper_range_refused(self):
        self.data["snapshot"]["state"][5] = 101
        self.data["target_feedback_state"][5] = 101
        self.assertFalse(self.check()["proposed_start_check_passed"])

    def test_file_review_no_mutations_subprocess_or_network(self):
        paths = list((HERE / "field_run").glob("*.json"))
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with patch("subprocess.run", side_effect=AssertionError("subprocess 금지")), \
             patch("socket.socket", side_effect=AssertionError("네트워크 금지")), \
             patch.object(Path, "write_text", side_effect=AssertionError("파일 쓰기 금지")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(review.main(["--result", str(self.path)]), 0)
        self.assertFalse(json.loads(output.getvalue())["hardware_access"])
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


if __name__ == "__main__":
    unittest.main()
