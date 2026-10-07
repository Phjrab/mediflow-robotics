import unittest

from mediflow_vlm.evaluate import evaluate


class EvaluationTests(unittest.TestCase):
    def test_reports_balanced_accuracy_and_prediction_distribution(self) -> None:
        truth = [
            {
                "id": "a",
                "completion": {
                    "medicine_id": "A",
                    "orientation": "upright",
                    "target_bin": "BIN_A",
                    "grasp_region": "lid",
                },
            },
            {
                "id": "b",
                "completion": {
                    "medicine_id": "B",
                    "orientation": "fallen",
                    "target_bin": "BIN_B",
                    "grasp_region": "body",
                },
            },
        ]
        predictions = [
            {"id": "a", "prediction": truth[0]["completion"]},
            {"id": "b", "prediction": truth[0]["completion"]},
        ]
        report = evaluate(truth, predictions)
        self.assertEqual(report["field_balanced_accuracy"]["medicine_id"], 0.5)
        self.assertEqual(report["class_recall"]["medicine_id"], {"A": 1.0, "B": 0.0})
        self.assertEqual(report["predicted_distribution"]["medicine_id"], {"A": 2})

    def test_action_task_ignores_command_derived_fields(self) -> None:
        truth = [
            {
                "id": "a",
                "completion": {
                    "medicine_id": "C",
                    "orientation": "fallen",
                    "target_bin": "BIN_C",
                    "grasp_region": "body",
                },
            }
        ]
        predictions = [
            {
                "id": "a",
                "prediction": {"orientation": "fallen", "grasp_region": "body"},
            }
        ]
        report = evaluate(truth, predictions, task="action")
        self.assertEqual(report["task"], "action")
        self.assertEqual(report["exact_match_accuracy"], 1.0)
        self.assertEqual(set(report["field_accuracy"]), {"orientation", "grasp_region"})

    def test_grasp_task_evaluates_one_field(self) -> None:
        truth = [{"id": "a", "completion": {"grasp_region": "lid"}}]
        predictions = [{"id": "a", "prediction": {"grasp_region": "lid"}}]
        report = evaluate(truth, predictions, task="grasp")
        self.assertEqual(report["exact_match_accuracy"], 1.0)
        self.assertEqual(report["field_accuracy"], {"grasp_region": 1.0})


if __name__ == "__main__":
    unittest.main()
