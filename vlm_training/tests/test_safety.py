import unittest

from mediflow_vlm.safety import assess_action_safety, camera_family


class SafetyTests(unittest.TestCase):
    def test_camera_family(self) -> None:
        self.assertEqual(camera_family("http://127.0.0.1/astra.mjpg"), "astra")
        self.assertEqual(camera_family("/dev/video6"), "video6")

    def test_known_before_grasp_requires_review(self) -> None:
        report = assess_action_safety(
            {"orientation": "fallen", "grasp_region": "body"},
            camera="/dev/video6",
            capture_phase="before_grasp",
        )
        self.assertEqual(report["status"], "review_required")
        self.assertFalse(report["robot_motion_allowed"])

    def test_unknown_is_blocked(self) -> None:
        report = assess_action_safety(
            {"orientation": "fallen", "grasp_region": "unknown"},
            camera="/dev/video6",
            capture_phase="before_grasp",
        )
        self.assertEqual(report["status"], "blocked")

    def test_astra_is_blocked(self) -> None:
        report = assess_action_safety(
            {"orientation": "upright", "grasp_region": "lid"},
            camera="http://127.0.0.1:8010/astra.mjpg",
            capture_phase="before_grasp",
        )
        self.assertEqual(report["status"], "blocked")

    def test_realsense_is_blocked(self) -> None:
        report = assess_action_safety(
            {"orientation": "upright", "grasp_region": "body"},
            camera="realsense",
            capture_phase="before_grasp",
        )
        self.assertEqual(report["status"], "blocked")

    def test_video4_is_blocked_for_insufficient_evidence(self) -> None:
        report = assess_action_safety(
            {"orientation": "upright", "grasp_region": "lid"},
            camera="/dev/video4",
            capture_phase="before_grasp",
        )
        self.assertEqual(report["status"], "blocked")

    def test_after_grasp_is_observation_only(self) -> None:
        report = assess_action_safety(
            {"orientation": "upright", "grasp_region": "lid"},
            camera="/dev/video6",
            capture_phase="after_grasp",
        )
        self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
