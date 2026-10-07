import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import cv2


SCENE_RE = re.compile(r"^scene_(\d{6})\.jpg$")
TRIAL_RE = re.compile(r"^trial_(\d{6})\.mp4$")


class DatasetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.images = self.root / "images"
        self.videos = self.root / "videos"
        self.annotations = self.root / "annotations.jsonl"
        self.trials = self.root / "trials.jsonl"
        self.trash = self.root / "trash"
        self.session = self.root / "session.json"
        self._lock = threading.RLock()
        self.images.mkdir(parents=True, exist_ok=True)
        self.videos.mkdir(parents=True, exist_ok=True)
        self._write_session("active")

    def _write_session(self, status):
        current = {}
        if self.session.exists():
            try:
                current = json.loads(self.session.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                current = {}
        now = datetime.now(timezone.utc).isoformat()
        current.setdefault("started_at", now)
        current.update({"updated_at": now, "status": status})
        self._atomic_json(self.session, current)

    @staticmethod
    def _atomic_json(path, value):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def _next_number(self):
        numbers = []
        for path in self.images.iterdir():
            match = SCENE_RE.match(path.name)
            if match:
                numbers.append(int(match.group(1)))
        if self.annotations.exists():
            for row in self._rows(ignore_errors=True):
                match = SCENE_RE.match(Path(row.get("image", "")).name)
                if match:
                    numbers.append(int(match.group(1)))
        if self.trash.exists():
            for annotations in self.trash.glob("trial_*/annotations.jsonl"):
                for line in annotations.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    match = SCENE_RE.match(Path(row.get("image", "")).name)
                    if match:
                        numbers.append(int(match.group(1)))
        return max(numbers, default=0) + 1

    def next_trial(self):
        with self._lock:
            numbers = []
            for path in self.videos.iterdir():
                match = TRIAL_RE.match(path.name)
                if match:
                    numbers.append(int(match.group(1)))
            for row in self._trial_rows(ignore_errors=True):
                match = TRIAL_RE.match(Path(row.get("video", "")).name)
                if match:
                    numbers.append(int(match.group(1)))
            if self.trash.exists():
                for path in self.trash.glob("trial_*_*"):
                    match = re.match(r"^trial_(\d{6})_", path.name)
                    if match:
                        numbers.append(int(match.group(1)))
            number = max(numbers, default=0) + 1
            while (self.videos / f"trial_{number:06d}.mp4").exists():
                number += 1
            trial_id = f"trial_{number:06d}"
            return trial_id, self.videos / f".{trial_id}.tmp.mp4"

    def save(self, frame, labels, camera="/dev/video2", extra=None):
        with self._lock:
            number = self._next_number()
            while True:
                filename = f"scene_{number:06d}.jpg"
                final_path = self.images / filename
                if not final_path.exists():
                    break
                number += 1
            temp_path = self.images / f".{filename}.tmp.jpg"
            ok = cv2.imwrite(str(temp_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise RuntimeError("이미지 인코딩/저장에 실패했습니다.")
            height, width = frame.shape[:2]
            row = {
                "image": f"images/{filename}",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "camera": camera,
                "width": width,
                "height": height,
                "instruction": labels["instruction"],
                "answer": {key: labels[key] for key in (
                    "target_class", "object_state", "visibility", "graspability",
                    "destination", "destination_visibility", "grasp_strategy", "required_skill",
                    "capture_phase", "action_result", "needs_approval"
                )},
            }
            if extra:
                row.update(extra)
            try:
                os.replace(temp_path, final_path)
                with self.annotations.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                temp_path.unlink(missing_ok=True)
                final_path.unlink(missing_ok=True)
                raise
            self._write_session("active")
            return row

    def finalize_trial(
        self,
        trial_id,
        temp_video,
        markers,
        labels,
        camera,
        started_at,
        ended_at,
        fps,
        frame_count,
        width,
        height,
    ):
        """Commit one video and its three representative still frames."""
        with self._lock:
            if not re.fullmatch(r"trial_\d{6}", trial_id):
                raise ValueError("잘못된 trial_id입니다.")
            temp_video = Path(temp_video).resolve()
            if temp_video.parent != self.videos.resolve() or not temp_video.is_file():
                raise ValueError("완료할 임시 영상이 없습니다.")
            final_video = self.videos / f"{trial_id}.mp4"
            if final_video.exists():
                raise ValueError(f"{final_video.name}이 이미 존재합니다.")
            os.replace(temp_video, final_video)

            annotations = []
            for marker in markers:
                frame_labels = dict(labels)
                frame_labels["capture_phase"] = marker["capture_phase"]
                frame_labels["action_result"] = (
                    labels["action_result"]
                    if marker["capture_phase"] == "after_grasp"
                    else "running"
                )
                annotations.append(
                    self.save(
                        marker["frame"],
                        frame_labels,
                        camera=camera,
                        extra={
                            "trial_id": trial_id,
                            "video": f"videos/{final_video.name}",
                            "video_offset_seconds": round(float(marker["offset_seconds"]), 3),
                            "frame_selection": marker["selection"],
                        },
                    )
                )

            trial = {
                "trial_id": trial_id,
                "video": f"videos/{final_video.name}",
                "started_at": started_at,
                "ended_at": ended_at,
                "camera": camera,
                "fps": fps,
                "frame_count": frame_count,
                "duration_seconds": round(frame_count / fps, 3) if fps else 0,
                "width": width,
                "height": height,
                "instruction": labels["instruction"],
                "answer": {
                    key: labels[key]
                    for key in (
                        "target_class", "object_state", "visibility", "graspability",
                        "destination", "destination_visibility", "grasp_strategy",
                        "required_skill", "action_result", "needs_approval"
                    )
                },
                "keyframes": [
                    {
                        "image": row["image"],
                        "capture_phase": row["answer"]["capture_phase"],
                        "offset_seconds": row["video_offset_seconds"],
                        "selection": row["frame_selection"],
                    }
                    for row in annotations
                ],
            }
            with self.trials.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(trial, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_session("active")
            return trial, annotations

    def _rows(self, ignore_errors=False):
        if not self.annotations.exists():
            return []
        rows = []
        for line_number, line in enumerate(self.annotations.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if not ignore_errors:
                    raise ValueError(f"annotations.jsonl {line_number}행이 올바른 JSON이 아닙니다.")
        return rows

    def _trial_rows(self, ignore_errors=False):
        if not self.trials.exists():
            return []
        rows = []
        for line_number, line in enumerate(self.trials.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if not ignore_errors:
                    raise ValueError(f"trials.jsonl {line_number}행이 올바른 JSON이 아닙니다.")
        return rows

    def latest(self):
        rows = self._rows(ignore_errors=True)
        return rows[-1] if rows else None

    def latest_trial(self):
        rows = self._trial_rows(ignore_errors=True)
        return rows[-1] if rows else None

    def list_trials(self, limit=50):
        rows = self._trial_rows(ignore_errors=True)
        return list(reversed(rows[-max(1, int(limit)):]))

    @staticmethod
    def _atomic_jsonl(path, rows):
        temp = path.with_suffix(path.suffix + ".tmp")
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)

    def archive_trial(self, trial_id, expected_video):
        """Remove one exact trial from active data while keeping a recoverable copy."""
        with self._lock:
            if not re.fullmatch(r"trial_\d{6}", str(trial_id)):
                raise ValueError("잘못된 trial_id입니다.")
            trials = self._trial_rows()
            trial = next((row for row in trials if row.get("trial_id") == trial_id), None)
            if trial is None:
                raise KeyError(trial_id)
            if trial.get("video") != expected_video:
                raise ValueError("삭제 대상 영상이 변경되었습니다. 목록을 새로 고쳐 확인하세요.")

            annotations = self._rows()
            trial_annotations = [row for row in annotations if row.get("trial_id") == trial_id]
            if not trial_annotations:
                raise ValueError("영상에 연결된 핵심 프레임 기록을 찾을 수 없습니다.")
            video_path = (self.root / str(trial["video"])).resolve()
            if video_path.parent != self.videos.resolve() or not video_path.is_file():
                raise ValueError("삭제할 영상 파일이 없거나 경로가 잘못되었습니다.")
            image_paths = []
            for row in trial_annotations:
                image_path = (self.root / str(row.get("image", ""))).resolve()
                if image_path.parent != self.images.resolve() or not image_path.is_file():
                    raise ValueError(f"연결된 핵심 프레임을 찾을 수 없습니다: {row.get('image')}")
                image_paths.append(image_path)

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_dir = self.trash / f"{trial_id}_{stamp}"
            suffix = 1
            while archive_dir.exists():
                archive_dir = self.trash / f"{trial_id}_{stamp}_{suffix}"
                suffix += 1
            archive_dir.mkdir(parents=True)
            (archive_dir / "images").mkdir()
            shutil.copy2(video_path, archive_dir / video_path.name)
            for image_path in image_paths:
                shutil.copy2(image_path, archive_dir / "images" / image_path.name)
            self._atomic_json(archive_dir / "trial.json", trial)
            self._atomic_jsonl(archive_dir / "annotations.jsonl", trial_annotations)
            self._atomic_json(
                archive_dir / "deletion.json",
                {
                    "trial_id": trial_id,
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "deleted_from_capture_ui",
                    "recoverable": True,
                },
            )

            self._atomic_jsonl(
                self.annotations,
                [row for row in annotations if row.get("trial_id") != trial_id],
            )
            self._atomic_jsonl(
                self.trials,
                [row for row in trials if row.get("trial_id") != trial_id],
            )
            video_path.unlink()
            for image_path in image_paths:
                image_path.unlink()
            self._write_session("active")
            return {
                "trial": trial,
                "annotations": trial_annotations,
                "archived_to": archive_dir.relative_to(self.root).as_posix(),
                "recoverable": True,
            }

    def delete_latest(self, expected_image):
        with self._lock:
            rows = self._rows()
            if not rows:
                raise ValueError("삭제할 촬영이 없습니다.")
            latest = rows[-1]
            if latest.get("image") != expected_image:
                raise ValueError("최근 촬영이 변경되었습니다. 목록을 새로 고친 뒤 다시 확인하세요.")
            image_path = (self.root / expected_image).resolve()
            if image_path.parent != self.images.resolve():
                raise ValueError("잘못된 이미지 경로입니다.")
            # JSONL을 먼저 원자적으로 갱신하고, 그 다음 이 도구가 만든 정확한 파일 하나만 삭제한다.
            self._atomic_jsonl(self.annotations, rows[:-1])
            image_path.unlink(missing_ok=True)
            self._write_session("active")
            return latest

    def stats(self):
        rows = self._rows(ignore_errors=True)
        counts = {}
        missing = []
        for row in rows:
            answer = row.get("answer", {})
            key = f'{answer.get("target_class", "unknown")} / {answer.get("object_state", "unknown")}'
            counts[key] = counts.get(key, 0) + 1
            relative = row.get("image", "")
            path = (self.root / relative).resolve()
            if path.parent != self.images.resolve() or not path.is_file():
                missing.append(relative)
        referenced = {row.get("image") for row in rows}
        orphaned = [f"images/{p.name}" for p in self.images.glob("scene_*.jpg") if f"images/{p.name}" not in referenced]
        trials = self._trial_rows(ignore_errors=True)
        missing_videos = []
        for trial in trials:
            relative = trial.get("video", "")
            path = (self.root / relative).resolve()
            if path.parent != self.videos.resolve() or not path.is_file():
                missing_videos.append(relative)
        referenced_videos = {trial.get("video") for trial in trials}
        orphaned_videos = [
            f"videos/{path.name}"
            for path in self.videos.glob("trial_*.mp4")
            if f"videos/{path.name}" not in referenced_videos
        ]
        return {
            "total": len(rows),
            "video_trials": len(trials),
            "counts": counts,
            "missing_images": missing,
            "orphaned_images": orphaned,
            "missing_videos": missing_videos,
            "orphaned_videos": orphaned_videos,
        }

    def close(self):
        self._write_session("closed")
