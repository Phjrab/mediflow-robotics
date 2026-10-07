import unittest

from mediflow_vlm.analyze_action import analyze


class AnalyzeActionTests(unittest.TestCase):
    def test_slices_and_collects_errors(self) -> None:
        truth = [
            {
                "id": "one",
                "image": "data/pilot/images/one.jpg",
                "camera": "/dev/video6",
                "source": {"capture_phase": "before_grasp"},
                "completion": {"orientation": "fallen", "grasp_region": "body"},
            },
            {
                "id": "two",
                "image": "data/pilot/images/two.jpg",
                "camera": "/dev/video6",
                "source": {"capture_phase": "before_grasp"},
                "completion": {"orientation": "upright", "grasp_region": "lid"},
            },
        ]
        predictions = [
            {"id": "one", "prediction": {"orientation": "fallen", "grasp_region": "body"}},
            {"id": "two", "prediction": {"orientation": "fallen", "grasp_region": "body"}},
        ]
        report, errors = analyze(truth, predictions)
        self.assertEqual(report["overall"]["exact_accuracy"], 0.5)
        self.assertEqual(report["slices"]["camera"]["video6"]["samples"], 2)
        self.assertEqual(errors[0]["id"], "two")


if __name__ == "__main__":
    unittest.main()
