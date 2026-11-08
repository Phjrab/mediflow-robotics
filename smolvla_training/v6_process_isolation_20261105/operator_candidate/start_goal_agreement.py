"""시작 목표 차이의 두 상한을 검사한다. 이 검사만으로 실행 권한을 주지 않는다.
"""
import argparse
import json
import math
from pathlib import Path

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
MAX_RAW_DIFFERENCE = 1
MAX_NORMALIZED_DIFFERENCE = 0.1


def vector(values, size):
    if not isinstance(values, (tuple, list)) or len(values) != size:
        raise ValueError("관절 차원 불일치")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError("수치 형식 또는 NaN/Inf 오류")
    return values


def assess(present_raw, target_raw, current_state, target_state, bounds):
    for values in (present_raw, target_raw):
        if not isinstance(values, dict) or set(values) != set(JOINTS):
            raise ValueError("원시 관절 이름 불일치")
        if any(type(v) is not int or not 0 <= v <= 4095 for v in values.values()):
            raise ValueError("원시 위치 형식 또는 범위 오류")
    current_state, target_state = vector(current_state, 6), vector(target_state, 6)
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 6:
        raise ValueError("관절 한계 차원 불일치")
    rows = []
    for i, joint in enumerate(JOINTS):
        lo, hi = vector(bounds[i], 2)
        if lo >= hi:
            raise ValueError("관절 한계 순서 오류")
        delta_raw = target_raw[joint] - present_raw[joint]
        delta = target_state[i] - current_state[i]
        inside = lo <= current_state[i] <= hi and lo <= target_state[i] <= hi
        reasons = []
        if abs(delta_raw) > MAX_RAW_DIFFERENCE:
            reasons.append("RAW_DIFFERENCE_GT_ONE")
        if abs(delta) > MAX_NORMALIZED_DIFFERENCE:
            reasons.append("NORMALIZED_DIFFERENCE_GT_POINT_ONE")
        if not inside:
            reasons.append("OUTSIDE_CALIBRATION_BOUNDS")
        if i == 5 and not (0 <= current_state[i] <= 100 and 0 <= target_state[i] <= 100):
            reasons.append("GRIPPER_RANGE")
        rows.append(dict(joint=joint, present_raw=present_raw[joint], target_raw=target_raw[joint],
                         delta_raw=delta_raw, delta=delta, unit="degree" if i < 5 else "percent_0_100",
                         exact=delta_raw == 0, check_passed=not reasons, reasons=reasons))
    return dict(scope="START_POSITION_AGREEMENT_ONLY",
                current_raw_exact=all(r["exact"] for r in rows),
                start_check_passed=all(r["check_passed"] for r in rows),
                max_raw_difference=MAX_RAW_DIFFERENCE,
                max_normalized_difference=MAX_NORMALIZED_DIFFERENCE,
                hardware_access=False, live_authorized=False, rows=rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    inspection = result["inspection"]
    review = assess(inspection["present_raw"], inspection["target_feedback_raw"],
                    inspection["snapshot"]["state"], inspection["target_feedback_state"],
                    inspection["joint_bounds"])
    review["normalized_values_source"] = "저장된 snapshot.state와 target_feedback_state. 재접속·재측정 없음."
    print(json.dumps(review, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
