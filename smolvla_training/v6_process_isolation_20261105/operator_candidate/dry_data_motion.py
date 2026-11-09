"""Exercise the same command planner against the saved field predictions."""

import argparse
import json
from pathlib import Path

from data_motion import Axis, Tracking, Hold
from data_motion_evidence import ARCHIVE_SHA, code_hashes, digest, inputs, read
from first_trial_core import JOINTS


CHUNKS_SHA = "10c6b0da58a1fe755d095da31ecf110e792a47e750206ccf724935ff8c73e69b"


def replay(chunks, axes, rates, start, measured, frozen):
    tracking = Tracking(axes, rates, start, measured, 0.)
    steps = 0
    now = 0.
    error = None
    rows = []
    try:
        for chunk in chunks:
            tracking.begin_chunk()
            for action in chunk["prediction"]["raw_actions"]:
                now += 1/30
                gaps = tracking.observe(measured, now)
                row = tracking.propose(action, measured, gaps, now)
                rows.append(row)
                if row["status"] != "READY":
                    raise Hold("가상 재생 목표 보류")
                tracking.sent(row["planned_wire_raw"], now)
                steps += 1
                if not frozen:
                    measured = tracking.previous
            for _ in range(17):
                now += 1/30
                tracking.observe(measured, now)
    except Hold as exc:
        error = dict(reason=str(exc), observed=getattr(exc, "observed", None))
    return dict(simulated_commands=steps, simulated_tracking_ratios=tracking.ratios(), error=error, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stats", "servo", "archive", "chunks", "out"):
        parser.add_argument("--"+name, required=True, type=Path)
    args = parser.parse_args()
    rates, _, values = inputs(args.stats, args.servo)
    if digest(args.archive) != ARCHIVE_SHA or digest(args.chunks) != CHUNKS_SHA:
        raise Hold("저장 실기 궤적 해시 불일치")
    archived = read(args.archive)
    chunks = read(args.chunks)
    bounds = archived["inspection"]["joint_bounds"]
    axes = [Axis(values["Min_Position_Limit"][name], values["Max_Position_Limit"][name], *pair)
            for name, pair in zip(JOINTS, bounds)]
    initial = archived["inspection"]["present_raw"]
    state = archived["inspection"]["snapshot"]["state"]
    if any(abs(axis.decode(initial[name])-actual) > 1e-8 for name, axis, actual in zip(JOINTS, axes, state)):
        raise Hold("저장 위치와 재생 인코더 불일치")
    ideal = replay(chunks, axes, rates, initial, state, frozen=False)
    stalled = replay(chunks, axes, rates, initial, state, frozen=True)
    if (ideal["error"] is not None or ideal["simulated_commands"] != 300
        or ((stalled.get("error") or {}).get("observed") or {}).get("code") != "ELBOW_UNDERTRACKING"):
        raise Hold(f"재생 검증 실패: 이상적 {ideal['simulated_commands']}회 {ideal['error']}; 정지 {stalled['simulated_commands']}회 {stalled['error']}")
    report = dict(status="DATA_REPLAY_DRY_PASS", hardware_access=False, motor_goal_packets=0,
                  actual_tracking_ratios=None, ideal=ideal, frozen_elbow=stalled,
                  stats_sha256=digest(args.stats), servo_sha256=digest(args.servo),
                  archive_sha256=digest(args.archive), code=code_hashes(Path(__file__).resolve().parent))
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps(dict(status=report["status"], actual_tracking_ratios=None,
                          ideal_commands=ideal["simulated_commands"], stalled_commands=stalled["simulated_commands"],
                          stall=stalled["error"], motor_goal_packets=0), ensure_ascii=True))


if __name__ == "__main__":
    main()
