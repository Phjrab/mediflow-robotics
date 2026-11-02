"""Append-only logical session ledger. No hardware access or inferred old sessions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid
import re
import warnings

from vla_record import RecordError, commit_saved_episode, load_counter, read_json, separate_paths, unique_json


def new_run(session_id=None, started_at=None):
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    moment = datetime.fromisoformat(started_at)
    if moment.tzinfo is None or (session_id is not None and not session_id.strip()):
        raise RecordError("timezone/ nonempty session required")
    run_id = moment.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:12]
    # Sequence 001 is scoped to this unique run, not guessed from historical dates.
    return {"run_id": run_id, "session_id": session_id or run_id + "-001", "started_at": started_at}


def validate_sessions_path(path, dataset, counter=None):
    path = Path(path)
    if path.is_symlink():
        raise RecordError("sidecar symlink forbidden")
    _, resolved = separate_paths(dataset, path)
    if counter is not None:
        counter = Path(counter).resolve()
        if resolved == counter or resolved in counter.parents or counter in resolved.parents or resolved == counter.with_name(counter.name + ".lock"):
            raise RecordError("sidecar/counter overlap")
    return resolved


def read_sessions(path, *, require_committed=True):
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise RecordError("partial sidecar line")
    entries = [json.loads(line, object_pairs_hook=unique_json) for line in raw.decode().splitlines()]
    seen = set()
    for row in entries:
        required = {"schema_version", "episode_index", "dataset_binding", "counter_path", "run_id", "session_id", "combo", "task", "started_at", "camera_config_sha256", "episode_fingerprint"}
        if not isinstance(row, dict) or set(row) != required or type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise RecordError("invalid session schema")
        binding = row["dataset_binding"]
        if not isinstance(binding, dict) or set(binding) != {"root", "repo_id"} or any(not isinstance(v,str) or not v.strip() for v in binding.values()):
            raise RecordError("invalid session dataset binding")
        if not Path(binding["root"]).is_absolute():
            raise RecordError("absolute dataset binding required")
        if not isinstance(row["counter_path"],str) or not Path(row["counter_path"]).is_absolute():
            raise RecordError("explicit absolute counter path required")
        for field in ("run_id", "session_id", "task", "combo", "started_at"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise RecordError("invalid session identity")
        for field in ("episode_fingerprint", "camera_config_sha256"):
            if not isinstance(row[field], str) or not re.fullmatch(r"[a-f0-9]{64}",row[field]):
                raise RecordError("invalid session fingerprint")
        if datetime.fromisoformat(row["started_at"]).tzinfo is None:
            raise RecordError("session timestamp timezone required")
        key = (binding["root"], row["episode_index"])
        if type(row["episode_index"]) is not int or row["episode_index"] < 0 or key in seen or not row["session_id"] or not row["task"]:
            raise RecordError("duplicate/invalid session entry")
        seen.add(key)
    if require_committed:
        groups={}
        for row in entries: groups.setdefault(row["counter_path"],[]).append(row)
        for counter_path, records in groups.items():
            if not Path(counter_path).is_file():
                raise RecordError("uncommitted sidecar: counter missing")
            raw=read_json(counter_path)
            counter=load_counter(counter_path,Path(records[0]["dataset_binding"]["root"]),raw["repo_id"])
            if counter["revision"]!=len(records):
                raise RecordError("uncommitted/partial sidecar-counter pair")
            for row,event in zip(records,counter["episodes"]):
                if row["dataset_binding"]!={"root":counter["dataset_root"],"repo_id":counter["actual_repo_id"]} or (row["episode_index"],row["combo"],row["task"],row["episode_fingerprint"])!=(event["index"],event["combo"],event["task"],event["fingerprint"]):
                    raise RecordError("uncommitted sidecar evidence disagreement")
    return entries


@contextmanager
def ledger_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RecordError("ledger busy/stale lock; reconcile manually") from exc
    os.close(fd)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            warnings.warn("ledger lock cleanup failed; reconcile before retry", RuntimeWarning)


def atomic_append_bytes(path, data):
    """Caller holds ledger lock. Old bytes remain an exact prefix; no row updates."""
    path = Path(path)
    old = path.read_bytes() if path.exists() else b""
    if old and not old.endswith(b"\n"):
        raise RecordError("partial ledger, refusing append")
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(old + data)
            stream.flush()
            os.fsync(stream.fileno())
        if (path.read_bytes() if path.exists() else b"") != old:
            raise RecordError("ledger changed concurrently")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def commit_with_session(counter_path, dataset, repo_id, before, expected_revision, combo, task,
                        *, sessions_path, run, camera_config_sha256, **kwargs):
    import re
    dataset = Path(dataset).resolve()
    sessions_path = validate_sessions_path(sessions_path, dataset, counter_path)
    if not re.fullmatch(r"[a-f0-9]{64}", camera_config_sha256):
        raise RecordError("camera config hash required")
    if set(run) != {"run_id", "session_id", "started_at"} or any(not isinstance(x, str) or not x for x in run.values()):
        raise RecordError("invalid run identity")
    if datetime.fromisoformat(run["started_at"]).tzinfo is None:
        raise RecordError("run timestamp timezone required")
    with ledger_lock(sessions_path):
        current = load_counter(counter_path, dataset, repo_id)
        entries = read_sessions(sessions_path,require_committed=False)
        # No backfill: existing counter-only episodes need explicit reconciliation.
        if len(entries) != current["revision"] or current["revision"] != expected_revision:
            raise RecordError("counter/sidecar mismatch; partial commit or duplicate retry")
        for row, event in zip(entries, current["episodes"]):
            if row["counter_path"] != str(Path(counter_path).resolve()) or row["dataset_binding"] != {"root": str(dataset), "repo_id": current["actual_repo_id"]} or row["episode_index"] != event["index"] or row["episode_fingerprint"] != event["fingerprint"] or row["task"] != event["task"] or row["combo"] != event["combo"]:
                raise RecordError("counter/sidecar evidence disagreement")
        def append_session(updated):
            event = updated["episodes"][-1]
            row = {"schema_version": 1, "episode_index": event["index"],
                   "dataset_binding": {"root": str(dataset), "repo_id": updated["actual_repo_id"]},
                   "counter_path":str(Path(counter_path).resolve()),
                   **run, "combo": combo, "task": task, "camera_config_sha256": camera_config_sha256,
                   "episode_fingerprint": event["fingerprint"]}
            atomic_append_bytes(sessions_path, json.dumps(row, ensure_ascii=False, allow_nan=False).encode() + b"\n")
        # Sidecar first, counter second. A crash between them leaves a detectable
        # ahead-sidecar. Never auto-retry/auto-increment in that state.
        return commit_saved_episode(counter_path, dataset, repo_id, before, expected_revision, combo, task,
                                    before_counter_replace=append_session, **kwargs)
