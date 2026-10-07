import json
import unittest

from mediflow_vlm.schema import (
    completion_from_source,
    compose_command_action,
    extract_action_completion,
    extract_compact_completion,
    extract_json_object,
    extract_grasp_completion,
    format_compact_completion,
    format_action_completion,
    format_grasp_completion,
    validate_action_completion,
    validate_grasp_completion,
    validate_completion,
)


class SchemaTests(unittest.TestCase):
    def test_maps_source_labels(self) -> None:
        self.assertEqual(
            completion_from_source(
                {"target_class": "medicine_b", "object_state": "fallen"}
            ),
            {
                "medicine_id": "B",
                "orientation": "fallen",
                "target_bin": "BIN_B",
            },
        )

    def test_unknown_mapping_is_fail_closed(self) -> None:
        value = completion_from_source(
            {"target_class": "unexpected", "object_state": "not_visible"}
        )
        self.assertEqual(value["medicine_id"], "unknown")
        self.assertEqual(value["orientation"], "unknown")
        self.assertEqual(value["target_bin"], "NONE")

    def test_extracts_fenced_json(self) -> None:
        expected = {
            "medicine_id": "A",
            "orientation": "upright",
            "target_bin": "BIN_A",
        }
        text = "```json\n" + json.dumps(expected) + "\n```"
        self.assertEqual(extract_json_object(text), expected)
        self.assertTrue(validate_completion(expected)[0])

    def test_rejects_wrong_bin(self) -> None:
        valid, errors = validate_completion(
            {
                "medicine_id": "A",
                "orientation": "upright",
                "target_bin": "BIN_B",
            }
        )
        self.assertFalse(valid)
        self.assertTrue(any("mapping" in error for error in errors))

    def test_validates_grasp_region_when_requested(self) -> None:
        value = {
            "medicine_id": "C",
            "orientation": "fallen",
            "target_bin": "BIN_C",
            "grasp_region": "body",
        }
        self.assertTrue(validate_completion(value, require_grasp_region=True)[0])
        del value["grasp_region"]
        self.assertFalse(validate_completion(value, require_grasp_region=True)[0])

    def test_compact_completion_round_trip_derives_bin(self) -> None:
        value = {
            "medicine_id": "C",
            "orientation": "fallen",
            "target_bin": "BIN_C",
            "grasp_region": "lid",
        }
        self.assertEqual(format_compact_completion(value), "C|fallen|lid")
        self.assertEqual(extract_compact_completion("C|fallen|lid"), value)

    def test_rejects_invalid_compact_completion(self) -> None:
        with self.assertRaises(ValueError):
            extract_compact_completion("A|upright")
        with self.assertRaises(ValueError):
            extract_compact_completion("D|upright|lid")

    def test_action_completion_round_trip(self) -> None:
        value = {"orientation": "fallen", "grasp_region": "body"}
        self.assertEqual(format_action_completion(value), "fallen|body")
        self.assertEqual(extract_action_completion("fallen|body"), value)
        self.assertTrue(validate_action_completion(value)[0])

    def test_rejects_invalid_action_completion(self) -> None:
        with self.assertRaises(ValueError):
            extract_action_completion("fallen")
        with self.assertRaises(ValueError):
            extract_action_completion("tilted|handle")

    def test_combines_command_medicine_with_action(self) -> None:
        self.assertEqual(
            compose_command_action(
                "B", {"orientation": "fallen", "grasp_region": "body"}
            ),
            {
                "medicine_id": "B",
                "orientation": "fallen",
                "target_bin": "BIN_B",
                "grasp_region": "body",
            },
        )

    def test_rejects_invalid_command_medicine(self) -> None:
        with self.assertRaises(ValueError):
            compose_command_action(
                "D", {"orientation": "fallen", "grasp_region": "body"}
            )

    def test_grasp_completion_round_trip(self) -> None:
        value = {"grasp_region": "unknown"}
        self.assertEqual(format_grasp_completion(value), "unknown")
        self.assertEqual(extract_grasp_completion("unknown"), value)
        self.assertTrue(validate_grasp_completion(value)[0])


if __name__ == "__main__":
    unittest.main()
