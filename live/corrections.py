"""Small, recorded operator corrections on the existing guarded motor connection."""

import hashlib
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


FPS = 30
CAMERAS = ("end_effector", "ceiling_vertical", "ceiling_oblique")
CLIP_ID = re.compile(r"[a-f0-9]{32}")
MAX_ARM_STEP = 2.0
MAX_GRIP_STEP = 3.0
GRIP_CEILING = 36.0


def load_forward_profile(path, calibration):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("calibration_sha256") != hashlib.sha256(calibration.read_bytes()).hexdigest():
        raise ValueError("앞으로 경로와 현재 팔로워 보정이 다릅니다")
    start, target = (data[key] for key in ("start", "target"))
    if any(not isinstance(row, list) or len(row) != 6
           or any(type(value) not in (int, float) or not math.isfinite(value) for value in row)
           for row in (start, target)):
        raise ValueError("앞으로 경로는 관절값 6개씩 필요합니다")
    change = max(abs(a - b) for a, b in zip(start[:5], target[:5]))
    if not 0.2 <= change <= MAX_ARM_STEP or abs(start[-1] - target[-1]) > 0.25:
        raise ValueError("앞으로 경로는 팔 관절별 최대 2°이며 그리퍼는 고정해야 합니다")
    return start, target


def target_for(io, sample, kind, forward):
    current = sample["state"]
    target = list(current)
    if kind == "gripper_open":
        target[-1] = min(current[-1] + MAX_GRIP_STEP, GRIP_CEILING, io.bounds[-1][1])
        if target[-1] - current[-1] < 0.35:
            raise ValueError("그리퍼 교정 개방 한도에 도달했습니다")
        changed = (5,)
    elif kind == "forward" and forward is not None:
        start, taught = forward
        if max(abs(a - b) for a, b in zip(start[:5], current[:5])) > 0.5 or abs(start[-1] - current[-1]) > 0.8:
            raise ValueError("현재 자세가 등록한 앞으로 경로의 시작점에서 벗어났습니다")
        target[:5] = taught[:5]
        if max(abs(a - b) for a, b in zip(target[:5], current[:5])) > MAX_ARM_STEP:
            raise ValueError("앞으로 교정이 관절별 2° 한도를 넘습니다")
        changed = tuple(range(5))
    else:
        raise ValueError("현재 세션에서 허용된 교정이 아닙니다")
    if any(not low <= value <= high for value, (low, high) in zip(target, io.bounds, strict=True)):
        raise ValueError("교정 목표 또는 현재 자세가 보정 범위를 벗어났습니다")
    return target, changed


def write_clip(directory, request, task, rows, outcome):
    import cv2

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / request["id"]
    path.mkdir(mode=0o700)
    writers = {}
    try:
        for role in CAMERAS:
            writer = cv2.VideoWriter(str(path / f"{role}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                     FPS, (640, 480))
            writers[role] = writer
            if not writer.isOpened():
                raise RuntimeError(f"{role} 영상 인코더를 열지 못했습니다")
        with (path / "frames.jsonl").open("x", encoding="utf-8") as target:
            for row, frames in rows:
                for role, writer in writers.items():
                    writer.write(cv2.cvtColor(frames[role]["pixels"], cv2.COLOR_RGB2BGR))
                target.write(json.dumps(row, allow_nan=False) + "\n")
    finally:
        for writer in writers.values():
            writer.release()
    manifest = {"schema": "so101.web_correction.v1", "id": request["id"],
                "instruction": request["text"], "kind": request["kind"],
                "run": request["run"], "task": task, "fps": FPS, "frames": len(rows),
                "cameras": list(CAMERAS), "outcome": outcome, "review": "pending"}
    pending = path / "manifest.pending"
    pending.write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    pending.replace(path / "manifest.json")
    return manifest


def execute(io, monitor, request, forward, deadline, log):
    """Retain the guarded connection and record only operator-owned samples."""
    from direct_live import JOINTS, Stop

    rows = []
    outcome = {"status": "interrupted", "reason": "교정 미완료"}
    initial = end = None
    try:
        before = io.checked()
        time.sleep(0.12)
        initial = io.checked()
        try:
            if max(abs(a - b) for a, b in zip(before["state"], initial["state"], strict=True)) > 0.4:
                raise ValueError("팔이 아직 움직이고 있어 교정 시작을 보류했습니다")
            if not 0 <= time.time() - request["requested"] < 2:
                raise ValueError("교정 요청이 만료되었습니다")
            start_raw = initial["present_raw"]
            if any(not low <= start_raw[joint] <= high for joint, (low, high) in io.raw_bounds.items()):
                raise ValueError("현재 팔이 교정 운용 범위 밖입니다")
            target, changed = target_for(io, initial, request["kind"], forward)
        except ValueError as error:
            return [], {"status": "rejected", "reason": str(error)}
        monitor.status("correcting", sample=initial, reason=None, force=True)
        start_time = time.monotonic()
        for index in range(22):
            next_tick = start_time + index / FPS
            time.sleep(max(0, min(next_tick, deadline) - time.monotonic()))
            if time.monotonic() >= deadline or monitor.hold_requested():
                raise Stop("교정 중 정지 요청 또는 시간 종료")
            if time.monotonic() - next_tick > 0.04:
                raise Stop("교정 녹화의 30 FPS 제어 주기가 지연되었습니다")
            sample = io.checked()
            measured = time.perf_counter()
            if any(value >= 50 for value in sample["temperatures"].values()):
                raise Stop("교정 중 온도 50°C 이상")
            if any(abs(sample["state"][i] - initial["state"][i]) > 0.8
                   for i in range(6) if i not in changed):
                raise Stop("교정 대상 밖 관절의 이동 감지")
            frames = io.correction_frames()
            if frames is None or time.perf_counter() - measured > 0.1:
                raise Stop("교정 기록에 필요한 새 카메라 프레임 없음")
            progress = min(1.0, index / 10)
            command = [a + (b - a) * progress for a, b in zip(initial["state"], target, strict=True)]
            raw = io.plan(command)["wire_raw"]
            for i, joint in enumerate(JOINTS):
                if i not in changed or index == 0:
                    raw[joint] = start_raw[joint]
            action = io.normalize(raw)
            if any(abs(action[i] - initial["state"][i]) > (MAX_GRIP_STEP if i == 5 else MAX_ARM_STEP) + 1e-6
                   for i in changed) or (request["kind"] == "gripper_open"
                                        and not initial["state"][-1] <= action[-1] <= GRIP_CEILING):
                raise Stop("정수 환산한 교정 목표가 이동 한도를 벗어났습니다")
            if monitor.hold_requested() or time.monotonic() >= deadline:
                raise Stop("교정 중 정지 요청 또는 시간 종료")
            transmitted = io.send(raw)
            sent = time.perf_counter()
            rows.append(({"frame": index, "state_at": measured, "sent_at": sent,
                          "observation.state": sample["state"], "action": action,
                          "present_raw": sample["present_raw"], "sent_raw": raw if transmitted is None else transmitted,
                          "camera_at": {role: frame["timestamp"] for role, frame in frames.items()}}, frames))
            end = sample["state"]
            shown = action if transmitted is None else [
                value if joint in transmitted else None for joint, value in zip(JOINTS, action, strict=True)]
            monitor.status("correcting", step=index + 1, sample=sample, sent=shown)
        if any(abs(end[i] - target[i]) > 0.8 for i in changed):
            raise Stop("실측 관절값이 교정 목표에 도달하지 않았습니다")
        if max(abs(end[i] - initial["state"][i]) for i in changed) < 0.2:
            raise Stop("실측 관절값에서 교정 움직임을 확인하지 못했습니다")
        outcome = {"status": "completed", "observed_end": end, "requested_target": target}
    except (Exception, KeyboardInterrupt) as error:
        outcome = {"status": "interrupted", "reason": str(error) or "Ctrl+C"}
    log({"event": "operator_correction", "id": request["id"], "kind": request["kind"], **outcome})
    return rows, outcome


def session(io, monitor, first, forward, deadline, log, *, one_shot=False):
    from direct_live import try_hold

    request = first
    outcomes = []
    hold = {"status": "NOT_NEEDED"}
    writer = ThreadPoolExecutor(max_workers=1)
    try:
        while time.monotonic() < deadline and not monitor.hold_requested():
            if request is None:
                sample = io.checked()
                monitor.status("operator_ready", sample=sample)
                time.sleep(0.05)
                request = monitor.take_correction()
                continue
            rows, outcome = execute(io, monitor, request, forward, deadline, log)
            outcomes.append({"id": request["id"], **outcome})
            monitor.correction_result = outcomes[-1]
            if outcome["status"] == "interrupted" and io.goal_attempted:
                hold = try_hold(io)
            monitor.status("saving_correction", reason=outcome.get("reason"), force=True)
            if rows:
                future = writer.submit(write_clip, monitor.directory / "corrections",
                                       request, monitor.task, rows, outcome)
                while not future.done() and hold["status"] == "NOT_NEEDED":
                    if monitor.hold_requested() or time.monotonic() >= deadline:
                        hold = try_hold(io)
                        break
                    io.checked()
                    time.sleep(0.03)
                try:
                    monitor.latest_clip = future.result()
                except Exception as error:
                    outcomes[-1]["recording_error"] = str(error)
                    break
            if one_shot or outcome["status"] == "interrupted" or hold["status"] != "NOT_NEEDED":
                break
            request = None
    except (Exception, KeyboardInterrupt) as error:
        outcomes.append({"status": "interrupted", "reason": str(error) or "Ctrl+C"})
        monitor.correction_result = outcomes[-1]
    finally:
        if io.goal_attempted and hold["status"] == "NOT_NEEDED":
            hold = try_hold(io)
        writer.shutdown()
    return outcomes, hold
