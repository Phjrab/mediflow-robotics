#!/usr/bin/env python3
"""Stage 2 planning and accounting. CLI is dry-run by default; execution is blocked.

No robot/serial import, subprocess or camera open. Safety providers are internal
read-only interfaces, never user-supplied JSON approval files. Raw lerobot-record
is NOT a validated RobotController runtime and is never launched here.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
import time
import warnings

import yaml
from check_vla_cameras import UniqueLoader, load_config as load_cameras, ROLES

ROOT = Path(__file__).resolve().parents[1]
COMBOS = tuple(f"{b}:{c}" for b in "ABC" for c in ("red", "blue", "green"))
RUNTIME_BLOCK = "BLOCKED: recorder is not integrated with verified RobotController HOLD/2deg/55C/disconnect safety"
IDENTITY_NOTE = "First creation may stamp repo_id; capture actual identity from the verified recorder and reuse it on resume."


class RecordError(ValueError):
    pass


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecordError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_json,
                      parse_constant=lambda _: (_ for _ in ()).throw(RecordError("nonfinite JSON")))


def load_tasks(path):
    cfg = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader)
    required = {"schema_version", "language_mode", "sentence_selection", "random_seed", "targets", "imbalance_max_gap", "position_variations", "combinations"}
    if not isinstance(cfg, dict) or set(cfg) != required or type(cfg["schema_version"]) is not int or cfg["schema_version"] != 1:
        raise RecordError("invalid task config")
    if cfg["language_mode"] not in ("korean", "mixed") or cfg["sentence_selection"] not in ("cycle", "random"):
        raise RecordError("unsupported language/selection")
    if not integer(cfg["random_seed"]) or not integer(cfg["imbalance_max_gap"]):
        raise RecordError("invalid seed/imbalance threshold")
    targets = cfg["targets"]
    if not isinstance(targets, dict) or set(targets) != {"primary_per_combo", "secondary_per_combo"}:
        raise RecordError("invalid targets")
    if targets["primary_per_combo"] != 10 or type(targets["primary_per_combo"]) is not int or not integer(targets["secondary_per_combo"], 20) or targets["secondary_per_combo"] > 30:
        raise RecordError("targets must be primary 10 and secondary 20..30")
    if not isinstance(cfg["combinations"], dict) or set(cfg["combinations"]) != set(COMBOS):
        raise RecordError("exactly nine combinations required")
    all_text = []
    for combo in COMBOS:
        entry = cfg["combinations"][combo]
        if not isinstance(entry, dict) or set(entry) != {"ko", "en"}:
            raise RecordError("ko/en required")
        for lang, size in (("ko", 2), ("en", 1)):
            if not isinstance(entry[lang], list) or len(entry[lang]) != size:
                raise RecordError("two Korean and one English sentence required")
            for sentence in entry[lang]:
                if not isinstance(sentence, str) or not sentence.strip() or sentence != sentence.strip():
                    raise RecordError("blank/padded sentence")
                all_text.append(sentence)
    if len(set(all_text)) != 27:
        raise RecordError("task sentences must be unique across combinations")
    hints = cfg["position_variations"]
    if not isinstance(hints, list) or not hints or any(not isinstance(x, str) or not x.strip() for x in hints):
        raise RecordError("position guidance required")
    return cfg


def choose_combo(counts, expanded=False, manual=None):
    allowed = COMBOS if expanded else ("A:red",)
    if manual is not None:
        if manual not in allowed:
            raise RecordError("manual combo requires --expand-nine for non-A:red tasks")
        return manual
    return min(allowed, key=lambda c: (counts[c], COMBOS.index(c)))


def choose_sentence(cfg, combo, count):
    entry = cfg["combinations"][combo]
    texts = entry["ko"] + (entry["en"] if cfg["language_mode"] == "mixed" else [])
    if cfg["sentence_selection"] == "cycle":
        return texts[count % len(texts)]
    # Seed+combo+completed count is stable across Python processes and retries.
    rng = random.Random(f'{cfg["random_seed"]}:{combo}:{count}')
    return rng.choice(texts)


def imbalance(counts, threshold):
    gap = max(counts.values()) - min(counts.values())
    return {"gap": gap, "threshold": threshold, "warning": gap > threshold}


def separate_paths(dataset, counter):
    dataset, counter = Path(dataset).resolve(), Path(counter).resolve()
    if dataset == counter or dataset in counter.parents or counter in dataset.parents:
        raise RecordError("counter and dataset paths must not overlap")
    if Path(counter).is_dir():
        raise RecordError("counter must be a file")
    return dataset, counter


def new_counter(dataset, repo_id):
    return {"schema_version": 1, "dataset_root": str(dataset), "repo_id": repo_id,
            "actual_repo_id": None, "revision": 0, "counts": dict.fromkeys(COMBOS, 0), "episodes": []}


def validate_actual_id(requested, actual):
    if not isinstance(actual, str):
        raise RecordError("actual recorder dataset identity unavailable")
    if actual == requested:
        return actual
    if not actual.startswith(requested + "_"):
        raise RecordError("actual dataset identity differs from requested base")
    try:
        suffix = actual[len(requested)+1:]
        if not re.fullmatch(r"\d{8}_\d{6}", suffix):
            raise ValueError()
        datetime.strptime(suffix, "%Y%m%d_%H%M%S")
    except ValueError as exc:
        raise RecordError("invalid official timestamp suffix") from exc
    return actual


def load_counter(path, dataset, repo_id):
    if Path(path).is_symlink():
        raise RecordError("counter symlinks are not accepted")
    value = read_json(path) if Path(path).exists() else new_counter(dataset, repo_id)
    if not isinstance(value, dict) or set(value) != set(new_counter(dataset, repo_id)):
        raise RecordError("corrupt counter schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["dataset_root"] != str(dataset) or value["repo_id"] != repo_id:
        raise RecordError("counter dataset binding mismatch")
    if not integer(value["revision"]) or not isinstance(value["episodes"], list) or value["revision"] != len(value["episodes"]):
        raise RecordError("counter revision mismatch")
    if value["revision"] == 0:
        if value["actual_repo_id"] is not None:
            raise RecordError("empty counter cannot claim saved recorder identity")
    else:
        validate_actual_id(repo_id, value["actual_repo_id"])
    counts = dict.fromkeys(COMBOS, 0)
    for index, episode in enumerate(value["episodes"]):
        if not isinstance(episode, dict) or set(episode) != {"index", "combo", "task", "fingerprint"}:
            raise RecordError("invalid counter episode")
        if type(episode["index"]) is not int or episode["index"] != index or episode["combo"] not in counts or not isinstance(episode["task"], str) or not episode["task"].strip() or not re.fullmatch(r"[a-f0-9]{64}", str(episode["fingerprint"])):
            raise RecordError("counter episode integrity mismatch")
        counts[episode["combo"]] += 1
    if not isinstance(value["counts"], dict) or value["counts"] != counts or any(not integer(n) for n in value["counts"].values()):
        raise RecordError("counter counts mismatch")
    return value


@dataclass(frozen=True)
class Snapshot:
    root: str
    frames: int
    episodes: tuple  # (task, frame_count, content_fingerprint) for each ordered episode


def parquet_rows(path):
    import pyarrow.parquet as pq  # optional; never required for an empty dry-run
    return pq.read_table(path).to_pylist()


def payload(root, relative):
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size == 0:
        raise RecordError("missing/empty/out-of-root dataset payload")
    return path


def read_snapshot(dataset, rows=parquet_rows):
    """Read-only v3 metadata+row+required-file check, not video decode or task success."""
    root = Path(dataset).resolve()
    if not root.exists():
        return Snapshot(str(root), 0, ())
    info = read_json(payload(root, "meta/info.json"))
    if info.get("codebase_version") != "v3.0" or not integer(info.get("total_episodes")) or not integer(info.get("total_frames")):
        raise RecordError("unsupported/incomplete dataset metadata")
    keys = {f"observation.images.{role}" for role in ROLES}
    features = info.get("features", {})
    if {k for k in features if k.startswith("observation.images.")} != keys:
        raise RecordError("dataset camera keys must be wrist/top/oblique")
    for key in ("action", "observation.state"):
        if features.get(key, {}).get("shape") != [6]:
            raise RecordError("state/action shape must be six")
    task_rows = rows(payload(root, "meta/tasks.parquet"))
    tasks = {}
    for task in task_rows:
        index, sentence = task.get("task_index"), task.get("task", task.get("__index_level_0__"))
        if not integer(index) or index in tasks or not isinstance(sentence, str) or not sentence.strip():
            raise RecordError("invalid task table")
        tasks[index] = sentence
    metas = []
    for path in sorted((root / "meta/episodes").rglob("*.parquet")):
        metas.extend(rows(payload(root, path.relative_to(root))))
    if len(metas) != info["total_episodes"]:
        raise RecordError("episode metadata count mismatch")
    metas.sort(key=lambda e: e["episode_index"])
    episodes, total = [], 0
    for index, meta in enumerate(metas):
        length = meta.get("length")
        if meta["episode_index"] != index or not integer(length, 1) or meta.get("dataset_from_index") != total or meta.get("dataset_to_index") != total + length:
            raise RecordError("invalid episode index/length/offset")
        data_path = info["data_path"].format(chunk_index=meta["data/chunk_index"], file_index=meta["data/file_index"])
        frames = [r for r in rows(payload(root, data_path)) if r["episode_index"] == index]
        if len(frames) != length or len(meta.get("tasks", [])) != 1:
            raise RecordError("episode frame/task count mismatch")
        previous = -1.0
        for fi, frame in enumerate(frames):
            stamp = frame.get("timestamp")
            if frame.get("frame_index") != fi or frame.get("index") != total + fi or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 or stamp <= previous:
                raise RecordError("frame index/timestamp mismatch")
            previous = stamp
            if tasks.get(frame.get("task_index")) != meta["tasks"][0]:
                raise RecordError("frame task mismatch")
            for key in ("action", "observation.state"):
                values = frame.get(key)
                if not isinstance(values, list) or len(values) != 6 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                    raise RecordError("nonfinite/missing state or action")
        videos = {}
        for key in sorted(keys):
            prefix = "videos/" + key
            start, end = meta[prefix + "/from_timestamp"], meta[prefix + "/to_timestamp"]
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
                raise RecordError("invalid video interval")
            path = info["video_path"].format(video_key=key, chunk_index=meta[prefix + "/chunk_index"], file_index=meta[prefix + "/file_index"])
            payload(root, path)
            videos[key] = (path, start, end)
        episodes.append((meta["tasks"][0], length, digest({"frames": frames, "videos": videos})))
        total += length
    if total != info["total_frames"]:
        raise RecordError("total frame count mismatch")
    return Snapshot(str(root), total, tuple(episodes))


def confirm_save(before, after, task, exit_code, completed, interrupted):
    if exit_code != 0 or completed is not True or interrupted is not False:
        raise RecordError("failed/interrupted/not-completed recording cannot count")
    if before.root != after.root or len(after.episodes) != len(before.episodes) + 1 or after.episodes[:-1] != before.episodes:
        raise RecordError("not exactly one append to unchanged episodes")
    last_task, length, fingerprint = after.episodes[-1]
    if last_task != task or not task.strip() or not integer(length, 1) or after.frames - before.frames != length or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise RecordError("new episode content mismatch")


def commit_saved_episode(counter_path, dataset, repo_id, before, expected_revision,
                         combo, task, *, exit_code, completed, interrupted,
                         snapshot_reader=read_snapshot, tasks_config=None, identity_reader=None,
                         before_counter_replace=None):
    """Internal future-recorder hook; no CLI success-file/override. Tested with fixtures.

    Live recorder integration and completion event remain blocked in Stage 2.
    All writers must use this lock. Stale locks are never automatically removed.
    """
    if Path(counter_path).is_symlink():
        raise RecordError("counter symlink rejected")
    dataset, counter_path = separate_paths(dataset, counter_path)
    if combo not in COMBOS or before.root != str(dataset):
        raise RecordError("invalid combo/dataset binding")
    tasks_config = tasks_config or load_tasks(ROOT / "configs/vla_tasks.yaml")
    entry = tasks_config["combinations"][combo]
    if task not in entry["ko"] + entry["en"]:
        raise RecordError("task does not belong to selected combo")
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    lock = counter_path.with_name(counter_path.name + ".lock")
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RecordError("counter busy or stale lock; manual reconciliation required") from exc
    temp, committed = None, False
    try:
        os.close(lock_fd)
        current = load_counter(counter_path, dataset, repo_id)
        initial_digest = digest(current)
        if current["revision"] != expected_revision or len(before.episodes) != expected_revision:
            raise RecordError("stale counter or dataset; reconcile without overwriting")
        if [e["fingerprint"] for e in current["episodes"]] != [e[2] for e in before.episodes]:
            raise RecordError("counter does not match dataset evidence")
        after = snapshot_reader(dataset)
        confirm_save(before, after, task, exit_code, completed, interrupted)
        # DatasetInfo does not persist repo_id: future adapter must supply actual
        # in-memory identity, not reconstruct it from time or guess from the root.
        if identity_reader is None:
            raise RecordError("verified recorder identity provider not connected")
        actual_id = validate_actual_id(repo_id, identity_reader(dataset))
        if current["actual_repo_id"] is not None and current["actual_repo_id"] != actual_id:
            raise RecordError("resume changed actual repo_id")
        current["actual_repo_id"] = actual_id
        current["episodes"].append({"index": expected_revision, "combo": combo, "task": task,
                                    "fingerprint": after.episodes[-1][2]})
        current["revision"] += 1
        current["counts"][combo] += 1
        # File fsync precedes atomic replace. Replace is the logical commit point.
        fd, temp = tempfile.mkstemp(prefix=counter_path.name + ".", suffix=".tmp", dir=counter_path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(load_counter(counter_path, dataset, repo_id)) != initial_digest or snapshot_reader(dataset) != after:
            raise RecordError("dataset/counter changed during commit; no counter replacement")
        if before_counter_replace is not None:
            before_counter_replace(current)
        os.replace(temp, counter_path)
        temp = None
        committed = True
        return current
    finally:
        if temp is not None:
            Path(temp).unlink(missing_ok=True)
        try:
            lock.unlink()
        except OSError:
            if not committed:
                raise
            warnings.warn("Counter committed but lock cleanup failed; reconcile stale lock before reuse.")


class UnavailableReadOnlyProvider:
    """No hardware access; cannot turn a JSON document into live safety evidence."""
    def observe(self):
        return {"observed_at_monotonic": time.monotonic(), "camera_stage1_pass": False,
                "canonical_ports_distinct": False, "exclusive_ownership": None,
                "temperatures_c": None, "pipeline_a_red_verified": False}


def preflight(cameras, *, first_motion_approved=False, expanded=False, provider=None):
    reasons = []
    if not first_motion_approved:
        reasons.append("first motion approval missing")
    if any(cameras["cameras"][role]["device"] is None for role in ROLES):
        reasons.append("camera identities unverified")
    try:
        evidence = (provider or UnavailableReadOnlyProvider()).observe()
        age = time.monotonic() - evidence["observed_at_monotonic"]
        if not math.isfinite(age) or not 0 <= age <= 2:
            reasons.append("stale preflight observation")
        for key in ("camera_stage1_pass", "canonical_ports_distinct", "exclusive_ownership"):
            if evidence.get(key) is not True:
                reasons.append(key + " unverified")
        temps = evidence.get("temperatures_c")
        if not isinstance(temps, list) or len(temps) != 6 or any(type(t) not in (float, int) or not math.isfinite(t) or not 0 <= t < 50 for t in temps):
            reasons.append("six start temperatures must be freshly measured below 50C")
        if expanded and evidence.get("pipeline_a_red_verified") is not True:
            reasons.append("A:red real pipeline not verified for nine-combo execution")
    except Exception:
        reasons.append("read-only live evidence unavailable")
    # Unconditional until a reviewed RobotController recorder adapter exists.
    # A user flag, fake JSON, or all-green mock cannot override this block.
    reasons.append(RUNTIME_BLOCK)
    return reasons


def build_command(repo_id, dataset, task, cameras, resume):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id) or not task.strip():
        raise RecordError("invalid repo id or blank task")
    camera_args = {role: {"type": "opencv", "index_or_path": cfg["device"],
                          "width": cfg["width"], "height": cfg["height"], "fps": cfg["fps"]}
                   for role, cfg in cameras["cameras"].items()}
    fps = {c["fps"] for c in camera_args.values()}
    if len(fps) != 1:
        raise RecordError("recording requires common camera fps")
    return ["lerobot-record", "--robot.type=so101_follower", "--robot.port=UNVERIFIED_FOLLOWER",
            "--teleop.type=so101_leader", "--teleop.port=UNVERIFIED_LEADER",
            "--robot.cameras=" + json.dumps(camera_args), "--dataset.repo_id=" + repo_id,
            "--dataset.root=" + str(dataset), "--dataset.single_task=" + task,
            "--dataset.num_episodes=1", "--dataset.push_to_hub=false",
            "--dataset.fps=" + str(next(iter(fps))), "--resume=" + str(resume).lower()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "configs/vla_tasks.yaml")
    parser.add_argument("--cameras", type=Path, default=ROOT / "configs/vla_cameras.yaml")
    parser.add_argument("--dataset-root", type=Path, default=ROOT / ".local/vla_datasets/medicine_sorting_vla_v1")
    parser.add_argument("--repo-id", default="local/medicine_sorting_vla_v1")
    parser.add_argument("--counter", type=Path, default=ROOT / ".local/vla_counters/medicine_sorting_vla_v1.json")
    parser.add_argument("--sessions", type=Path, help="explicit sidecar outside dataset; required for future recording")
    parser.add_argument("--session-id", help="physical capture session; absent means this run only")
    parser.add_argument("--combo", choices=COMBOS)
    parser.add_argument("--expand-nine", action="store_true", help="planning only; live pipeline gate is separate")
    parser.add_argument("--target-stage", choices=("primary", "secondary"), default="primary")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--first-motion-approved", action="store_true", help="not sufficient to unlock execution")
    args = parser.parse_args(argv)
    try:
        tasks, cameras = load_tasks(args.tasks), load_cameras(args.cameras)
        if args.counter.is_symlink():
            raise RecordError("counter symlink rejected")
        dataset, counter = separate_paths(args.dataset_root, args.counter)
        if args.sessions is not None:
            from vla_sessions import validate_sessions_path
            validate_sessions_path(args.sessions, dataset, counter)
        state = load_counter(counter, dataset, args.repo_id)
        before = read_snapshot(dataset)
        if len(before.episodes) != state["revision"] or [x[2] for x in before.episodes] != [x["fingerprint"] for x in state["episodes"]]:
            raise RecordError("dataset/counter stale; read-only reconciliation needed")
        combo = choose_combo(state["counts"], args.expand_nine, args.combo)
        task = choose_sentence(tasks, combo, state["counts"][combo])
        target = tasks["targets"][args.target_stage + "_per_combo"]
        result = {"mode": "execute" if args.execute else "dry-run", "status": "BLOCKED" if args.execute else "PLANNED_ONLY",
                  "combo": combo, "task": task, "dataset_root": str(dataset), "counter_revision": state["revision"],
                  "remaining": {c: max(0, target - state["counts"][c]) for c in COMBOS},
                  "target_per_combo": target, "target_total": 9 * target,
                  "imbalance": imbalance(state["counts"], tasks["imbalance_max_gap"]),
                  "pipeline_verified": False, "guidance": tasks["position_variations"],
                  "readiness_blocks": [RUNTIME_BLOCK], "identity_note": IDENTITY_NOTE,
                  "command_is_executable": False,
                  "command_draft": build_command(state["actual_repo_id"] or args.repo_id, dataset, task, cameras, dataset.exists()),
                  "note": "Draft only; unresolved ports and RobotController safety integration. No data/counter changes."}
        if args.execute:
            result["blocked_reasons"] = preflight(cameras, first_motion_approved=args.first_motion_approved, expanded=args.expand_nine)
            if args.sessions is None:
                result["blocked_reasons"].append("explicit --sessions path required")
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 2 if args.execute else 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, RecordError) else "invalid/unavailable local input; no recorder launched"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
