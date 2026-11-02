"""Read-only LeRobot v3 evidence primitives; never imports robot or model code."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from vla_record import RecordError, read_json, payload, digest, ROOT

JOINTS = [n + ".pos" for n in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")]
LEGACY_CAMERAS = {"end_effector": "wrist", "ceiling_vertical": "top", "ceiling_oblique": "oblique"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_hash(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}


def outside(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if root == output or root in output.parents or output in root.parents:
        raise RecordError("output must be outside source dataset")
    return output


def new_output(root, output):
    output = outside(root, output)
    output.mkdir(parents=True, exist_ok=False)
    return output


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


class Dataset:
    def __init__(self, root):
        import pyarrow.parquet as pq
        self.root = Path(root).resolve()
        self.info = read_json(payload(self.root, "meta/info.json"))
        self.metas = []
        for p in sorted((self.root / "meta/episodes").rglob("*.parquet")):
            self.metas.extend(pq.read_table(payload(self.root, p.relative_to(self.root))).to_pylist())
        self.metas.sort(key=lambda x: x["episode_index"])
        self.tasks = {r["task_index"]: r.get("task", r.get("__index_level_0__")) for r in pq.read_table(payload(self.root, "meta/tasks.parquet")).to_pylist()}
        self.cache = {}
        self.binding = {"root": str(self.root), "metadata_sha256": digest({"info": self.info, "episodes": self.metas, "tasks": self.tasks})}

    def rows(self, meta):
        import pyarrow.parquet as pq
        relative = self.info["data_path"].format(chunk_index=meta["data/chunk_index"], file_index=meta["data/file_index"])
        if relative not in self.cache:
            table = pq.read_table(payload(self.root, relative)).to_pylist()
            grouped = {}
            for row in table:
                grouped.setdefault(row["episode_index"], []).append(row)
            self.cache[relative] = grouped
        return self.cache[relative].get(meta["episode_index"], [])

    def fingerprint(self, meta):
        videos = {}
        for key in sorted(self.camera_keys):
            path, start, end = self.video(meta, key)
            videos[key] = (str(path.relative_to(self.root)), start, end)
        return digest({"frames": self.rows(meta), "videos": videos})

    def video(self, meta, key):
        prefix = "videos/" + key
        path = payload(self.root, self.info["video_path"].format(video_key=key,
            chunk_index=meta[prefix+"/chunk_index"], file_index=meta[prefix+"/file_index"]))
        return path, float(meta[prefix+"/from_timestamp"]), float(meta[prefix+"/to_timestamp"])

    @property
    def camera_keys(self):
        return [k for k, v in self.info["features"].items() if v.get("dtype") == "video"]


def video_inventory(path, decode=True):
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        report = {"width": stream.width, "height": stream.height,
                  "fps": float(stream.average_rate) if stream.average_rate else None,
                  "container_frames": stream.frames, "decoded_pts": None}
        if decode:
            report["decoded_pts"] = [float(f.pts * f.time_base) for f in container.decode(stream) if f.pts is not None]
        return report


def samples(dataset, meta, count=3):
    """Decode selected per-episode frame ordinals using original PTS interval."""
    import av
    import numpy as np
    wanted = set(np.linspace(0, meta["length"]-1, max(3, count), dtype=int).tolist())
    result = {}
    for key in dataset.camera_keys:
        path, start, end = dataset.video(meta, key)
        images, idx = {}, 0
        with av.open(str(path)) as container:
            for frame in container.decode(video=0):
                if frame.pts is None:
                    continue
                t = float(frame.pts * frame.time_base)
                if t < start - 1e-5:
                    continue
                if t >= end - 1e-5:
                    break
                if idx in wanted:
                    images[idx] = frame.to_ndarray(format="rgb24")
                idx += 1
        if set(images) != wanted:
            raise RecordError("sample frames missing from video interval")
        result[key] = images
    return result


def sample_hashes(dataset, meta):
    return {key: {str(i): hashlib.sha256(a.tobytes()).hexdigest() for i, a in frames.items()}
            for key, frames in samples(dataset, meta).items()}


def official_worker(root, official_src, python=sys.executable, mode="reload", output=None, excluded=None):
    if official_src is None:
        return {"status": "NOT_RUN", "reason": "official v0.6.0 source path not supplied"}
    with tempfile.TemporaryDirectory(prefix="vla-loader-") as tmp:
        result = Path(tmp) / "result.json"
        env = dict(os.environ, PYTHONPATH=str(Path(official_src)/"src"), HF_HUB_OFFLINE="1",
                   HF_DATASETS_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1",
                   HF_DATASETS_CACHE=tmp + "/datasets", HF_HOME=tmp + "/hf")
        command = [str(python), "-B", str(ROOT/"scripts/vla_official_worker.py"), "--root", str(root),
                   "--source", str(official_src), "--mode", mode, "--result", str(result)]
        if output is not None:
            command += ["--out", str(output), "--excluded", json.dumps(excluded or [])]
        try:
            proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120 if mode == "derive" else 45)
            report = read_json(result) if result.exists() else {"status": "FAIL", "reason": "worker did not return structured result"}
            report["exit_code"] = proc.returncode
            return report
        except subprocess.TimeoutExpired:
            return {"status": "FAIL", "reason": "official loader timeout"}
