import unittest

from mediflow_vlm.dataset import split_grouped_rows
from mediflow_vlm.make_pilot import select_pilot_rows
from mediflow_vlm.train_qlora import _balance_training_rows


class DatasetSplitTests(unittest.TestCase):
    def test_sessions_never_cross_splits(self) -> None:
        rows = []
        for session_index in range(12):
            for sample_index in range(2):
                rows.append(
                    {
                        "session_id": f"session_{session_index:03d}",
                        "camera": f"camera_{session_index % 2}",
                        "answer": {
                            "target_class": f"medicine_{'abc'[session_index % 3]}",
                            "object_state": "upright" if sample_index == 0 else "fallen",
                        },
                    }
                )
        assignment = split_grouped_rows(
            rows,
            {"train": 0.7, "validation": 0.15, "test": 0.15},
            seed=101,
        )
        self.assertEqual(len(assignment), 12)
        self.assertEqual(set(assignment.values()), {"train", "validation", "test"})

    def test_pilot_filter_excludes_unclear_and_duplicate_rows(self) -> None:
        base = {
            "source": {"capture_phase": "before_grasp"},
            "completion": {"medicine_id": "A", "orientation": "upright"},
            "near_duplicate_of": None,
            "qa_flags": [],
        }
        duplicate = {**base, "near_duplicate_of": "scene_1"}
        tilted = {**base, "completion": {"medicine_id": "A", "orientation": "tilted"}}
        selected, excluded = select_pilot_rows([base, duplicate, tilted])
        self.assertEqual(selected, [base])
        self.assertEqual(excluded["near_duplicate_candidate"], 1)
        self.assertEqual(excluded["orientation_not_upright_or_fallen"], 1)

    def test_balance_training_rows_keeps_epoch_size_and_equalizes_combinations(self) -> None:
        rows = []
        for medicine, grasp, count in (("A", "body", 5), ("B", "lid", 2), ("C", "body", 1)):
            rows.extend(
                {
                    "id": f"{medicine}-{grasp}-{index}",
                    "completion": {"medicine_id": medicine, "grasp_region": grasp},
                }
                for index in range(count)
            )
        balanced = _balance_training_rows(rows, "medicine-grasp", seed=101)
        self.assertEqual(len(balanced), len(rows))
        counts = {}
        for row in balanced:
            key = (row["completion"]["medicine_id"], row["completion"]["grasp_region"])
            counts[key] = counts.get(key, 0) + 1
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertEqual(
            [row["id"] for row in balanced],
            [row["id"] for row in _balance_training_rows(rows, "medicine-grasp", seed=101)],
        )


if __name__ == "__main__":
    unittest.main()
