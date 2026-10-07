from __future__ import annotations

import json
import os
import threading
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import load_jsonl, write_jsonl
from .schema import BIN_MAP, MEDICINE_VALUES, ORIENTATION_VALUES, validate_completion


REVIEW_STATUSES = frozenset({"pending", "approved", "excluded"})
GRASP_REGIONS = frozenset({"lid", "body", "unknown"})


class ReviewStore:
    """Append-only review decisions over immutable prepared manifests."""

    def __init__(
        self,
        project_root: Path,
        manifest: Path = Path("dataset_v2/review_manifest.jsonl"),
        decisions: Path = Path("dataset_v2/review_decisions.jsonl"),
    ) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = self._resolve(manifest)
        if not self.manifest_path.exists() and manifest == Path("dataset_v2/review_manifest.jsonl"):
            self.manifest_path = self._resolve(Path("dataset_v2/manifest.jsonl"))
        self.decisions_path = self._resolve(decisions)
        self._lock = threading.Lock()
        self.rows = self._load_manifest()
        self.by_id = {row["id"]: row for row in self.rows}
        self.decisions = self._load_decisions()

    def _resolve(self, path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _load_manifest(self) -> list[dict[str, Any]]:
        rows = load_jsonl(self.manifest_path)
        seen: set[str] = set()
        for row in rows:
            row.pop("_source_line", None)
            sample_id = row.get("id")
            if not isinstance(sample_id, str) or not sample_id:
                raise ValueError("every review row must have a non-empty id")
            if sample_id in seen:
                raise ValueError(f"duplicate review id: {sample_id}")
            seen.add(sample_id)
            image_path = self.image_path(row)
            if not image_path.is_file():
                raise ValueError(f"missing review image for {sample_id}: {image_path}")
        return rows

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.decisions_path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for decision in load_jsonl(self.decisions_path):
            decision.pop("_source_line", None)
            sample_id = decision.get("id")
            if sample_id not in self.by_id:
                continue
            if decision.get("status") not in REVIEW_STATUSES:
                continue
            latest[sample_id] = decision
        return latest

    def image_path(self, row: dict[str, Any]) -> Path:
        image_path = (self.project_root / str(row["image"])).resolve()
        if image_path != self.project_root and self.project_root not in image_path.parents:
            raise ValueError(f"image path escapes project root: {row['image']}")
        return image_path

    def effective_item(self, sample_id: str) -> dict[str, Any]:
        row = self.by_id.get(sample_id)
        if row is None:
            raise KeyError(sample_id)
        decision = self.decisions.get(sample_id)
        source = row.get("source", {})
        item = {
            "id": row["id"],
            "image_url": f"/api/images/{row['id']}",
            "camera": row.get("camera"),
            "captured_at": row.get("captured_at"),
            "session_id": row.get("session_id"),
            "split": row.get("split"),
            "capture_phase": source.get("capture_phase"),
            "action_result": source.get("action_result"),
            "trial_id": source.get("trial_id"),
            "video_url": f"/api/videos/{row['id']}" if source.get("video") else None,
            "video_offset_seconds": source.get("video_offset_seconds"),
            "frame_selection": source.get("frame_selection"),
            "qa_flags": row.get("qa_flags", []),
            "near_duplicate_of": row.get("near_duplicate_of"),
            "original_completion": deepcopy(row["completion"]),
            "completion": deepcopy(row["completion"]),
            "review_status": "pending",
            "grasp_region": row.get("proposed_grasp_region")
            if row.get("proposed_grasp_region") in GRASP_REGIONS
            else None,
            "note": "",
            "decided_at": None,
        }
        if decision:
            decision_status = decision["status"]
            grasp_region = decision.get("grasp_region")
            # Decisions made before grasp-region review was introduced need one quick revisit.
            if decision_status == "approved" and grasp_region not in GRASP_REGIONS:
                decision_status = "pending"
            item["review_status"] = decision_status
            item["completion"] = deepcopy(decision.get("completion", row["completion"]))
            item["grasp_region"] = grasp_region if grasp_region in GRASP_REGIONS else None
            item["note"] = decision.get("note", "")
            item["decided_at"] = decision.get("decided_at")
        return item

    def video_path(self, row: dict[str, Any]) -> Path | None:
        relative = row.get("source", {}).get("video")
        if not relative:
            return None
        video_path = (self.project_root / str(relative)).resolve()
        if self.project_root not in video_path.parents or not video_path.is_file():
            return None
        return video_path

    def items(self) -> list[dict[str, Any]]:
        return [self.effective_item(row["id"]) for row in self.rows]

    def summary(self) -> dict[str, Any]:
        items = self.items()
        statuses = Counter(item["review_status"] for item in items)
        return {
            "total": len(items),
            "pending": statuses["pending"],
            "approved": statuses["approved"],
            "excluded": statuses["excluded"],
            "reviewed": statuses["approved"] + statuses["excluded"],
            "approved_by_split": dict(
                Counter(item["split"] for item in items if item["review_status"] == "approved")
            ),
        }

    def record(
        self,
        sample_id: str,
        status: str,
        medicine_id: str,
        orientation: str,
        grasp_region: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        if sample_id not in self.by_id:
            raise KeyError(sample_id)
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid review status: {status}")
        if medicine_id not in MEDICINE_VALUES:
            raise ValueError(f"invalid medicine_id: {medicine_id}")
        if orientation not in ORIENTATION_VALUES:
            raise ValueError(f"invalid orientation: {orientation}")
        if grasp_region is not None and grasp_region not in GRASP_REGIONS:
            raise ValueError(f"invalid grasp_region: {grasp_region}")
        if status == "approved" and grasp_region not in GRASP_REGIONS:
            raise ValueError("승인하려면 그리퍼 접근/파지 부위를 선택해야 합니다.")
        completion = {
            "medicine_id": medicine_id,
            "orientation": orientation,
            "target_bin": BIN_MAP[medicine_id],
        }
        valid, errors = validate_completion(completion)
        if not valid:
            raise ValueError("; ".join(errors))
        decision = {
            "id": sample_id,
            "status": status,
            "completion": completion,
            "grasp_region": grasp_region,
            "note": note.strip()[:500],
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
            with self.decisions_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self.decisions[sample_id] = decision
        return self.effective_item(sample_id)

    def export_approved(self) -> dict[str, Any]:
        exported: dict[str, list[dict[str, Any]]] = {
            str(row["split"]): [] for row in self.rows if row.get("split")
        }
        for source in self.rows:
            decision = self.decisions.get(source["id"])
            if (
                not decision
                or decision["status"] != "approved"
                or decision.get("grasp_region") not in GRASP_REGIONS
            ):
                continue
            split = source.get("split")
            if split not in exported:
                continue
            row = deepcopy(source)
            row["completion"] = deepcopy(decision["completion"])
            row["review_status"] = "approved"
            row["review"] = {
                "decided_at": decision["decided_at"],
                "grasp_region": decision["grasp_region"],
                "note": decision.get("note", ""),
                "original_completion": deepcopy(source["completion"]),
            }
            exported[split].append(row)

        output: dict[str, Any] = {"splits": {}}
        output_dir = self.manifest_path.parent
        for split, rows in exported.items():
            path = output_dir / f"reviewed_{split}.jsonl"
            write_jsonl(path, rows)
            output["splits"][split] = {"samples": len(rows), "path": str(path)}
        output["approved_total"] = sum(len(rows) for rows in exported.values())
        output["generated_at"] = datetime.now(timezone.utc).isoformat()
        report_path = output_dir / "review_export_report.json"
        report_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        output["report"] = str(report_path)
        return output
