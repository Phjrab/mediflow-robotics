import unittest

from mediflow_vlm.combine_predictions import combine_predictions


class CombinePredictionsTests(unittest.TestCase):
    def test_grasp_adapter_overrides_action_grasp(self) -> None:
        rows = combine_predictions(
            [{"id": "one", "prediction": {"orientation": "fallen", "grasp_region": "lid"}}],
            [{"id": "one", "prediction": {"grasp_region": "body"}}],
        )
        self.assertEqual(
            rows[0]["prediction"],
            {"orientation": "fallen", "grasp_region": "body"},
        )
        self.assertTrue(rows[0]["valid"])

    def test_missing_grasp_fails_closed(self) -> None:
        rows = combine_predictions(
            [{"id": "one", "prediction": {"orientation": "fallen", "grasp_region": "lid"}}],
            [],
        )
        self.assertIsNone(rows[0]["prediction"])
        self.assertFalse(rows[0]["valid"])


if __name__ == "__main__":
    unittest.main()
