import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mediflow_vlm.dataset import write_jsonl
from mediflow_vlm.review_app import create_app
from mediflow_vlm.review_store import ReviewStore


def sample_row(sample_id: str = "scene_000001", split: str = "train") -> dict:
    return {
        "id": sample_id,
        "image": f"data/pilot/images/{sample_id}.jpg",
        "captured_at": "2026-09-21T00:00:00+00:00",
        "camera": "/dev/video6",
        "session_id": "session_001",
        "split": split,
        "prompt": "classify",
        "completion": {
            "medicine_id": "A",
            "orientation": "upright",
            "target_bin": "BIN_A",
        },
        "review_status": "pending",
        "qa_flags": [],
        "near_duplicate_of": None,
        "source": {"line": 1, "capture_phase": "before_grasp"},
    }


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        image_dir = self.root / "data/pilot/images"
        image_dir.mkdir(parents=True)
        Image.new("RGB", (32, 24), "white").save(image_dir / "scene_000001.jpg")
        write_jsonl(self.root / "dataset_v2/manifest.jsonl", [sample_row()])
        self.store = ReviewStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_records_correction_without_changing_manifest(self) -> None:
        original = (self.root / "dataset_v2/manifest.jsonl").read_text(encoding="utf-8")
        item = self.store.record(
            "scene_000001", "approved", "B", "fallen", "lid", "corrected"
        )
        self.assertEqual(item["completion"]["target_bin"], "BIN_B")
        self.assertEqual(item["grasp_region"], "lid")
        self.assertEqual(self.store.summary()["approved"], 1)
        self.assertEqual(
            (self.root / "dataset_v2/manifest.jsonl").read_text(encoding="utf-8"), original
        )

        exported = self.store.export_approved()
        self.assertEqual(exported["approved_total"], 1)
        row = json.loads(
            (self.root / "dataset_v2/reviewed_train.jsonl").read_text(encoding="utf-8")
        )
        self.assertEqual(row["review_status"], "approved")
        self.assertEqual(row["completion"]["medicine_id"], "B")
        self.assertEqual(row["review"]["grasp_region"], "lid")
        self.assertEqual(row["review"]["original_completion"]["medicine_id"], "A")

    def test_rejects_invalid_label(self) -> None:
        with self.assertRaises(ValueError):
            self.store.record("scene_000001", "approved", "D", "upright")

    def test_approval_requires_grasp_region(self) -> None:
        with self.assertRaisesRegex(ValueError, "파지 부위"):
            self.store.record("scene_000001", "approved", "A", "upright")

    def test_review_api_and_image(self) -> None:
        client = create_app(self.store).test_client()
        page = client.get("/")
        self.assertIn("VLM 데이터 검수", page.get_data(as_text=True))
        self.assertIn("Space", page.get_data(as_text=True))
        self.assertNotIn("<kbd>Enter</kbd>", page.get_data(as_text=True))
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/images/scene_000001").status_code, 200)
        response = client.post(
            "/api/reviews/scene_000001",
            json={
                "status": "approved",
                "medicine_id": "C",
                "orientation": "fallen",
                "grasp_region": "body",
                "note": "looks correct",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["summary"]["approved"], 1)
        self.assertEqual(client.post("/api/export").status_code, 200)


if __name__ == "__main__":
    unittest.main()
