import unittest

from mediflow_vlm.audit_safety import audit


class AuditSafetyTests(unittest.TestCase):
    def test_never_allows_motion(self) -> None:
        truth = [
            {
                "id": "one",
                "image": "data/pilot/images/one.jpg",
                "camera": "/dev/video6",
                "source": {"capture_phase": "before_grasp"},
                "completion": {
                    "medicine_id": "B",
                    "target_bin": "BIN_B",
                    "orientation": "fallen",
                    "grasp_region": "body",
                },
            }
        ]
        predictions = [
            {
                "id": "one",
                "prediction": {"orientation": "fallen", "grasp_region": "body"},
            }
        ]
        report, queue = audit(truth, predictions)
        self.assertEqual(report["status_counts"], {"review_required": 1})
        self.assertEqual(report["robot_motion_allowed"], 0)
        self.assertEqual(queue[0]["decision"]["target_bin"], "BIN_B")


if __name__ == "__main__":
    unittest.main()
