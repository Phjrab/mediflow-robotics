#!/usr/bin/env python3
"""Read-only, per-episode VLA data audit. Outputs only to a fresh external folder."""
import argparse
from pathlib import Path
import json
import numpy as np

from vla_data import Dataset, JOINTS, LEGACY_CAMERAS, ROOT, now, sha, new_output, write_new, video_inventory, official_worker
from vla_record import load_tasks, read_json, RecordError
from vla_sessions import read_sessions


def units_evidence(dataset, evidence_path=None, bounds_path=None):
    result = {"status": "UNKNOWN", "reason": "no recording-time unit/calibration evidence", "limits": None}
    if evidence_path is None:
        return result
    try:
        e = read_json(evidence_path)
        if e["dataset_binding"] != dataset.binding or e["joint_names"] != JOINTS or e["arm_unit"] != "degree" or e["coordinate_system"] != "lerobot-degrees-midpoint-4095":
            raise ValueError("unit/order/coordinate/dataset binding mismatch")
        if not e["evidence"]:
            raise ValueError("file/line evidence required")
        for ref in e["evidence"]:
            path = Path(ref["file"])
            if sha(path) != ref["sha256"] or ref["line"] < 1 or ref["contains"] not in path.read_text().splitlines()[ref["line"]-1]:
                raise ValueError("unit evidence file/line/hash mismatch")
        result.update(status="VERIFIED_DECLARATION", evidence=e["evidence"], gripper_unit=e.get("gripper_unit"))
        if bounds_path is not None:
            b = read_json(bounds_path)
            if b["source_sha256"] != e["calibration_sha256"] or b["normalization_mode"] != "DEGREES" or b["resolution_steps"] != 4096:
                raise ValueError("calibration coordinate/source mismatch")
            labels = ["Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll"]
            if e.get("bounds_order") != labels:
                raise ValueError("explicit bounds-order evidence required")
            limits = []
            for name in labels:
                lo, hi = b["arm_ranges_raw"][name]
                midpoint = (lo+hi)/2
                limits.append([(lo-midpoint)*360/4095, (hi-midpoint)*360/4095])
            result.update(limits=limits, bounds_file=str(bounds_path), bounds_sha256=sha(bounds_path))
        return result
    except (KeyError, ValueError, OSError, IndexError) as exc:
        return {"status": "UNKNOWN", "reason": str(exc), "limits": None}


def audit(root, sessions=None, units=None, bounds=None, video_mode="full", dt_tolerance=0.2,
          length_method="mad", length_factor=4.0, idle_tolerance=0.1, jump_threshold=30.0,
          official_source=None, loader_python=None):
    ds = Dataset(root)
    if not ds.metas:
        raise RecordError("empty episode metadata")
    cfg = load_tasks(ROOT/"configs/vla_tasks.yaml")
    task_to_combo = {sentence: combo for combo, entry in cfg["combinations"].items() for sentence in entry["ko"]+entry["en"]}
    session_rows = read_sessions(sessions) if sessions else []
    session_map = {e["episode_index"]: e for e in session_rows if e["dataset_binding"]["root"] == str(ds.root)}
    if len(session_map) != len(session_rows):
        raise RecordError("session sidecar dataset binding mismatch")
    lengths = np.array([m["length"] for m in ds.metas], dtype=float)
    median = float(np.median(lengths)) if len(lengths) else 0.0
    mad = float(np.median(np.abs(lengths-median))) if len(lengths) else 0.0
    q1, q3 = np.percentile(lengths, [25,75]).tolist() if len(lengths) else [0,0]
    unit = units_evidence(ds, units, bounds)
    camera_names = {k.removeprefix("observation.images.") for k in ds.camera_keys}
    canonical = {"wrist", "top", "oblique"}
    legacy = camera_names == set(LEGACY_CAMERAS)
    inventory, episodes, initial_states = {}, [], []
    fps = ds.info.get("fps", 0)
    if type(fps) not in (int, float) or not np.isfinite(fps) or fps <= 0:
        raise RecordError("invalid dataset fps")
    total = 0
    for order, meta in enumerate(ds.metas):
        idx = meta["episode_index"]
        result = {"episode_index": idx, "reasons": [], "session_id": None, "complete_demonstration": "UNKNOWN_VIDEO_REVIEW_REQUIRED"}
        def add(code, severity="WARN", evidence=None):
            result["reasons"].append({"code": code, "severity": severity, "evidence": evidence})
        if idx != order or ds.info.get("total_episodes") != len(ds.metas) or ds.info.get("codebase_version") != "v3.0":
            add("META_SCHEMA_COUNT", "FAIL")
        if legacy:
            add("LEGACY_CAMERA_KEYS", evidence=LEGACY_CAMERAS)
        elif camera_names != canonical:
            add("CAMERA_KEYS_MISSING", "FAIL", sorted(camera_names))
        for feature in ("action", "observation.state"):
            spec = ds.info.get("features", {}).get(feature, {})
            if spec.get("shape") != [6] or spec.get("names") != JOINTS:
                add("JOINT_DIM_NAMES_ORDER", "FAIL", feature)
        if unit["status"] == "UNKNOWN":
            add("UNITS_UNVERIFIED")
        if unit["limits"] is None:
            add("CALIBRATION_COMPATIBILITY_UNKNOWN")
        try:
            rows = ds.rows(meta)
            n = len(rows)
            result["frames"] = n
            if n != meta["length"] or not n:
                add("FRAME_COUNT", "FAIL")
            if [r.get("frame_index") for r in rows] != list(range(n)) or [r.get("index") for r in rows] != list(range(total,total+n)):
                add("FRAME_MISSING_OR_INDEX", "FAIL")
            if meta.get("dataset_from_index") != total or meta.get("dataset_to_index") != total+n:
                add("EPISODE_OFFSET", "FAIL")
            stamps = np.array([r["timestamp"] for r in rows], dtype=float)
            if not np.isfinite(stamps).all() or (stamps < 0).any() or (np.diff(stamps) <= 0).any():
                add("TIMESTAMP_NONMONOTONIC", "FAIL")
            if n and (abs(stamps[0]) > dt_tolerance/fps or (np.abs(np.diff(stamps)-1/fps) > dt_tolerance/fps).any()):
                add("TIMESTAMP_INTERVAL", "FAIL")
            tasks = {ds.tasks.get(r.get("task_index")) for r in rows}
            if len(tasks) != 1 or None in tasks or any(not isinstance(t, str) or not t.strip() for t in tasks) or set(meta.get("tasks", [])) != tasks:
                add("TASK_MISSING_BLANK_MULTIPLE", "FAIL")
            task = next(iter(tasks)) if len(tasks) == 1 else None
            result["task"] = task
            result["combo"] = task_to_combo.get(task)
            if task and result["combo"] is None:
                add("TASK_NOT_VLA_VARIANT", evidence="legacy/single-task needs explicit review; no automatic labels")
            session = session_map.get(idx)
            if session:
                result["session_id"] = session["session_id"]
                if session["task"] != task or session["combo"] != result["combo"]:
                    add("SESSION_COMBO_TASK_MISMATCH", "FAIL")
                if session["episode_fingerprint"] != ds.fingerprint(meta):
                    add("SESSION_FINGERPRINT_MISMATCH", "FAIL")
            else:
                add("SESSION_UNKNOWN")
            arrays = {}
            for feature in ("observation.state", "action"):
                values = np.array([r[feature] for r in rows], dtype=float)
                arrays[feature] = values
                if values.shape != (n,6) or not np.isfinite(values).all():
                    add("STATE_ACTION_NONFINITE_DIM", "FAIL", feature)
                    continue
                delta = np.abs(np.diff(values, axis=0))
                result[feature] = {"min": values.min(axis=0).tolist(), "max": values.max(axis=0).tolist(),
                    "max_step": delta.max(axis=0).tolist() if len(delta) else [0]*6,
                    "units": "degree/declared gripper" if unit["status"] != "UNKNOWN" else "unknown native values"}
                if delta.size and delta.max() > jump_threshold:
                    add("JUMP_CANDIDATE", evidence={"feature": feature, "native_threshold": jump_threshold})
                if unit["limits"]:
                    limits = np.array(unit["limits"])
                    if ((values[:,:5] < limits[:,0]) | (values[:,:5] > limits[:,1])).any():
                        add("CALIBRATION_LIMIT_EXCEEDED", "FAIL", feature)
            state, action = arrays["observation.state"], arrays["action"]
            if n and state.shape == (n,6) and np.isfinite(state).all():
                initial_states.append(state[0])
                stationary = np.max(np.abs(state-state[0]), axis=1) <= idle_tolerance
                moving = np.flatnonzero(~stationary)
                result["leading_idle_frames"] = int(moving[0]) if len(moving) else n
                result["leading_idle_seconds"] = result["leading_idle_frames"]/fps
            if unit.get("gripper_unit") == "percent" and action.shape == (n,6) and np.isfinite(action).all():
                d = np.diff(action, axis=0)
                candidates = np.flatnonzero((d[:,5] > 10) & (np.max(np.abs(d[:,:5]),axis=1) > 1))
                if len(candidates):
                    add("EARLY_GRIPPER_OPEN_HEURISTIC", evidence={"frames": candidates.tolist(), "rule": "gripper +10pp while arm step >1deg; not a failure label"})
            else:
                add("GRIPPER_HEURISTIC_UNITS_UNKNOWN")
            total += n
        except (OSError, ValueError, KeyError, TypeError) as exc:
            add("DATA_READ_ERROR", "FAIL", type(exc).__name__)
        scale = mad if length_method == "mad" else q3-q1
        outlier = (abs(meta["length"]-median) > length_factor*max(scale,1)) if length_method=="mad" else (meta["length"] < q1-length_factor*max(scale,1) or meta["length"] > q3+length_factor*max(scale,1))
        if outlier:
            add("LENGTH_OUTLIER", evidence={"method": length_method, "median": median, "scale": scale})
        for key in ds.camera_keys:
            try:
                path, start, end = ds.video(meta, key)
                if str(path) not in inventory:
                    inventory[str(path)] = video_inventory(path, decode=video_mode == "full")
                video = inventory[str(path)]
                if not np.isfinite([start,end]).all() or not 0 <= start < end:
                    add("VIDEO_INTERVAL", "FAIL", key)
                if not video["fps"] or abs(video["fps"]-fps) > 0.1:
                    add("VIDEO_FPS", "FAIL", key)
                if ds.info["features"][key].get("shape") != [video["height"],video["width"],3]:
                    add("VIDEO_RESOLUTION", "FAIL", key)
                if video["decoded_pts"] is None:
                    add("VIDEO_FRAME_COUNT_NOT_RUN", evidence=key)
                else:
                    count = sum(start-1e-5 <= t < end-1e-5 for t in video["decoded_pts"])
                    if count != meta["length"]:
                        add("VIDEO_FRAME_COUNT", "FAIL", {"key": key,"video": count,"data": meta["length"]})
            except Exception as exc:
                add("VIDEO_MISSING_OR_UNREADABLE", "FAIL", {"key": key,"error_type": type(exc).__name__})
        add("DEMONSTRATION_SUCCESS_UNVERIFIED")
        result["status"] = "FAIL" if any(r["severity"] == "FAIL" for r in result["reasons"]) else "WARN" if result["reasons"] else "OK"
        episodes.append(result)
    if total != ds.info.get("total_frames"):
        for result in episodes:
            result["status"] = "FAIL"
            result["reasons"].append({"code": "TOTAL_FRAMES", "severity": "FAIL", "evidence": total})
    reload_result = official_worker(root, official_source, **({"python": loader_python} if loader_python else {}))
    return {"schema_version":1, "created_at":now(), "dataset_binding":ds.binding, "metadata":ds.info,
        "unit_evidence":unit, "camera_mapping_reference_only":LEGACY_CAMERAS if legacy else {},
        "criteria":{"dt_fraction":dt_tolerance,"length_method":length_method,"length_factor":length_factor,
                    "idle_native_tolerance":idle_tolerance,"jump_native_threshold":jump_threshold,"video_mode":video_mode},
        "length_summary":{"median":median,"mad":mad,"q1":q1,"q3":q3},
        "initial_state_variance":np.var(initial_states,axis=0).tolist() if initial_states else None,
        "reload":reload_result,"episodes":episodes,"summary":{s:sum(e["status"]==s for e in episodes) for s in ("OK","WARN","FAIL")}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True); p.add_argument("--sessions",type=Path)
    p.add_argument("--out",type=Path,default=Path("reports/audit")); p.add_argument("--units",type=Path)
    p.add_argument("--bounds",type=Path,default=ROOT/"configs/so101_follower_calibration_bounds_20261008.json")
    p.add_argument("--official-source",type=Path); p.add_argument("--loader-python")
    p.add_argument("--video-mode",choices=("full","metadata"),default="full")
    p.add_argument("--dt-tolerance",type=float,default=.2); p.add_argument("--length-method",choices=("mad","iqr"),default="mad")
    p.add_argument("--length-factor",type=float,default=4); p.add_argument("--jump-threshold",type=float,default=30)
    a=p.parse_args(argv)
    if any(not np.isfinite(x) or x<=0 for x in (a.dt_tolerance,a.length_factor,a.jump_threshold)):
        p.error("positive finite thresholds required")
    output=new_output(a.root,a.out)
    try:
        report=audit(a.root,a.sessions,a.units,a.bounds,a.video_mode,a.dt_tolerance,a.length_method,a.length_factor,
                     jump_threshold=a.jump_threshold,official_source=a.official_source,loader_python=a.loader_python)
        write_new(output/"DATASET_AUDIT.json",report)
        (output/"DATA_READINESS.md").write_text("# 데이터 준비 상태\n\n"+str(report["summary"])+"\n\n공식 reload: "+report["reload"]["status"]+
            "\n\n완전 시연 성공과 촬영 세션은 추측하지 않습니다. WARN은 학습 준비 완료가 아닙니다. 상세 reason code는 JSON을 확인하세요.\n",encoding="utf-8")
        print(json.dumps({"out":str(output),"summary":report["summary"],"reload":report["reload"]["status"]}))
        return 1 if report["summary"]["FAIL"] else 0
    except Exception as exc:
        if not (output/"DATASET_AUDIT.json").exists():
            write_new(output/"DATASET_AUDIT.json",{"status":"FAIL","error_type":type(exc).__name__,"reason":str(exc)[:400]})
        if not (output/"DATA_READINESS.md").exists():
            (output/"DATA_READINESS.md").write_text("# 데이터 감사 실패\n\n구조 검사 중 오류가 발생했습니다. 학습 준비 완료가 아닙니다. DATASET_AUDIT.json을 확인하세요.\n",encoding="utf-8")
        print("audit failed; see new report directory")
        return 2


if __name__=="__main__":
    raise SystemExit(main())
