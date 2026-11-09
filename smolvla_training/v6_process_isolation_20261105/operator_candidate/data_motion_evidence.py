"""Bind the learned limits, field readout and replay to a one-time test."""

import hashlib
import json
from pathlib import Path

from data_motion import load_profile
from first_trial_core import Hold, JOINTS, vector


STATS_SHA = "252d17501046f993510d044f56fe63c786d3a48ccbe4f7b64720d0abe6fff407"
SERVO_SHA = "8617e5bcea5fbfd925dfea4815a42dca390a05b4a11b8070198a567ec96ab7c7"
ARCHIVE_SHA = "c76bc807958dd4a759c00d8ba94be0572b385bf6d7ab92294f1c54cdcf0b48f4"
ARCHIVE_STOP_SHA = "8081b2c57792a526e234e6ee10791dc73b574bc4ad62731daa2e4c7e721edbd5"
CODE_FILES = ("data_motion.py", "data_motion_stage.py", "data_motion_evidence.py",
              "dry_data_motion.py", "trial_runtime.py", "goal_telemetry.py",
              "reviewed_start_stop.py", "first_trial_from_hold.py", "first_trial.py",
              "first_trial_core.py", "work_session.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def code_hashes(root):
    return {name: digest(root / name) for name in CODE_FILES}


def inputs(stats_path, servo_path):
    if digest(stats_path) != STATS_SHA:
        raise Hold("검증한 137회 학습 통계 해시 불일치")
    if digest(servo_path) != SERVO_SHA:
        raise Hold("검증한 6축 서보 보고서 해시 불일치")
    rates = load_profile(read(stats_path))
    servo = read(servo_path)
    if (servo.get("status") != "SERVO_CONFIG_READONLY_COMPLETE" or servo.get("motor_register_writes") != 0
        or servo.get("blocked_packets") != 0 or servo.get("cleanup_errors") != []
        or servo.get("port_closed") is not True or servo.get("requested_complete") is not True):
        raise Hold("6축 읽기 전용 진단 완료 근거 필요")
    values = servo["values"]
    current_limits = values["Protection_Current"]
    if (set(current_limits) != set(JOINTS)
        or any(type(value) is not int or not 0 < value <= 2047 for value in current_limits.values())):
        raise Hold("6축 전류 보호 한계 미확인")
    return rates, current_limits, values


def dry_gate(root, dry_path, stats_path, servo_path):
    inputs(stats_path, servo_path)
    report = read(dry_path)
    if (report.get("status") != "DATA_REPLAY_DRY_PASS" or report.get("motor_goal_packets") != 0
        or report.get("hardware_access") is not False or report.get("stats_sha256") != digest(stats_path)
        or report.get("servo_sha256") != digest(servo_path) or report.get("archive_sha256") != ARCHIVE_SHA
        or report.get("code") != code_hashes(root)):
        raise Hold("현재 코드·설정과 일치하는 무송신 dry-run 결과 필요")


def previous_live50(path, stats_path, servo_path, dry_path):
    report = read(path)
    elbow = report.get("tracking_ratios", {}).get("elbow_flex", {})
    ratio = elbow.get("ratio")
    if (report.get("status") != "DATA_LIVE_50_COMPLETE" or report.get("motor_goal_packets") != 50
        or report.get("torque_register_writes") != 0 or report.get("cleanup_errors") != []
        or report.get("ports_closed") != {"io": True, "probe": True}
        or report.get("data_evidence") != dict(stats=digest(stats_path), servo=digest(servo_path), dry=digest(dry_path))
        or type(ratio) not in (float, int) or not .5 <= ratio < float("inf")
        or elbow.get("status") != "ASSESSED" or elbow.get("cumulative_sent_change", 0) < 2 - 1e-8
        or elbow.get("commands", 0) < 30 or elbow.get("elapsed_seconds", 0) < 1 - 1e-8):
        raise Hold("300스텝 차단. 동일 프로필 50스텝 완료와 팔꿈치 추종 증거 필요")
    return vector(report["last_wire_target"])
