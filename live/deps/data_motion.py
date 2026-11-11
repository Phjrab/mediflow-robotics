"""Data-derived command limits and bounded tracking. No device imports."""

from dataclasses import dataclass
import math

from first_trial_core import Hold, JOINTS, bounds_gate, vector


STEP_LIMIT = 2.
WARNING_GAP = 15.
STOP_GAP = 25.
STOP_DURATION = .5
ELBOW = JOINTS.index("elbow_flex")
ELBOW_TRAVEL = 97.01098901098901
EPSILON = 1e-8


def hold(code, message, **details):
    error = Hold(message)
    error.observed = dict(code=code, **details)
    return error


def load_profile(stats):
    if [stats.get(key) for key in ("episodes", "frames", "pairs", "fps")] != [137, 63551, 63414, 30]:
        raise Hold("학습 통계의 회차, 프레임 또는 주기 불일치")
    rows = stats.get("joints", {})
    if set(rows) != set(JOINTS):
        raise Hold("학습 통계의 관절 누락")
    rates = []
    for name in JOINTS[:5]:
        p99 = rows[name]["per_second"]["p99"]
        if rows[name]["unit"] != "degree" or type(p99) not in (float, int) or not math.isfinite(p99) or p99 <= 0:
            raise Hold("관절별 p99 단위 또는 값 오류")
        rate = min(60., 1.2 * p99)
        if rows[name]["requested_limit"] != rate:
            raise Hold("산출 제한과 p99 불일치")
        rates.append(rate)
    return rates


@dataclass(frozen=True)
class Axis:
    raw_min: int
    raw_max: int
    at_min: float
    at_max: float

    def __post_init__(self):
        if (type(self.raw_min) is not int or type(self.raw_max) is not int
            or not 0 <= self.raw_min < self.raw_max <= 4095
            or not all(math.isfinite(value) for value in (self.at_min, self.at_max))
            or self.at_min == self.at_max):
            raise Hold("관절 보정과 정수 인코딩 불일치")

    @property
    def bounds(self):
        return tuple(sorted((self.at_min, self.at_max)))

    @property
    def scale(self):
        return (self.at_max - self.at_min) / (self.raw_max - self.raw_min)

    def decode(self, raw):
        if type(raw) is not int or not self.raw_min <= raw <= self.raw_max:
            raise Hold("정수 목표가 보정 범위를 벗어남")
        return self.at_min + (raw - self.raw_min) * self.scale

    def encode(self, value, low, high):
        def coordinate(position):
            return self.raw_min + (position - self.at_min) / self.scale

        left, right = sorted((coordinate(low), coordinate(high)))
        left = max(self.raw_min, math.ceil(left - EPSILON))
        right = min(self.raw_max, math.floor(right + EPSILON))
        if left > right:
            raise Hold("가동범위와 명령 제한에 맞는 정수 목표 없음")
        return min(right, max(left, round(coordinate(value))))


class Tracking:
    def __init__(self, axes, rates, initial_raw, measured, now):
        self.axes = list(axes)
        self.rates = list(rates)
        if (len(self.axes) != 6 or len(self.rates) != 5
            or any(type(rate) not in (int, float) or not 0 < rate <= 60 for rate in self.rates)):
            raise Hold("6축 보정 또는 관절별 속도 설정 오류")
        self.bounds = [axis.bounds for axis in axes]
        elbow_low, elbow_high = self.bounds[ELBOW]
        self.bounds[ELBOW] = (max(elbow_low, -ELBOW_TRAVEL), min(elbow_high, ELBOW_TRAVEL))
        if set(initial_raw) != set(JOINTS):
            raise Hold("시작 활성 목표 6축 누락")
        self.previous_raw = dict(initial_raw)
        self.previous = self.decode(initial_raw)
        bounds_gate(self.previous, self.bounds, source="initial_wire")
        self.commands = [self.previous]
        self.observations = []
        self.last_sent = None
        self.last_observed = None
        self.severe_since = [None] * 5
        self.begin_chunk()
        self.observe(measured, now)

    def begin_chunk(self):
        self.elbow_excited_at = None
        self.elbow_commands = [self.previous[ELBOW]]
        self.elbow_observations = []

    def decode(self, raw):
        return vector([axis.decode(raw[name]) for name, axis in zip(JOINTS, self.axes)])

    def observe(self, measured, now):
        measured = vector(measured)
        bounds_gate(measured, self.bounds, source="measured_state")
        if not math.isfinite(now) or self.last_observed is not None and now < self.last_observed:
            raise Hold("관측 시계 역행")
        self.last_observed = now
        self.observations.append(measured)
        self.elbow_observations.append(measured[ELBOW])
        gaps = [abs(a - b) for a, b in zip(self.previous, measured)]
        for i, gap in enumerate(gaps[:5]):
            if gap <= STOP_GAP:
                self.severe_since[i] = None
            elif self.severe_since[i] is None:
                self.severe_since[i] = now
            elif now - self.severe_since[i] >= STOP_DURATION - EPSILON:
                raise hold("TRACKING_GAP_PERSISTENT", "활성 목표 추종 차이 25° 초과가 0.5초 지속",
                           joint=JOINTS[i], gap=gap, seconds=now-self.severe_since[i])
        if self.elbow_excited_at is not None and now - self.elbow_excited_at >= STOP_DURATION:
            commanded = max(self.elbow_commands) - min(self.elbow_commands)
            observed = max(self.elbow_observations) - min(self.elbow_observations)
            elbow = dict(commanded_span=commanded, observed_span=observed, ratio=observed/commanded)
            if elbow["ratio"] < .5:
                raise hold("ELBOW_UNDERTRACKING", "팔꿈치 응답 부족. 서보·부하 원인 조사 필요", **elbow)
        return gaps

    def propose(self, action, measured, gaps, now):
        action = vector(action)
        dt = 1 / 30 if self.last_sent is None else now - self.last_sent
        if not math.isfinite(dt) or dt <= 0:
            raise Hold("목표 전송 간격 오류")
        raw, clamped, reasons = {}, [], {}
        for i, (name, axis, desired, last) in enumerate(zip(JOINTS, self.axes, action, self.previous)):
            lower, upper = self.bounds[i]
            reason = []
            if not lower <= desired <= upper:
                reason.append("elbow_travel_or_calibration" if i == ELBOW else "calibration")
            if abs(desired - last) > STEP_LIMIT + EPSILON:
                reason.append("previous_wire_step")
            if i < 5 and abs(desired - last) > self.rates[i] * dt + EPSILON:
                reason.append("joint_speed")
            budget = min(STEP_LIMIT, self.rates[i] * dt) if i < 5 else STEP_LIMIT
            low, high = max(lower, last-budget), min(upper, last+budget)
            if i == 5:
                # Existing gripper protection uses position points, not degrees.
                if abs(desired-measured[i]) > 2:
                    reason.append("gripper_current_limit")
                low, high = max(low, measured[i]-2), min(high, measured[i]+2)
            if low > high:
                raise hold("NO_SAFE_TARGET", "안전 목표 교집합 없음", joint=name)
            target = min(high, max(low, desired))
            raw[name] = axis.encode(target, low, high)
            encoded = axis.decode(raw[name])
            if not low-EPSILON <= encoded <= high+EPSILON:
                raise Hold("정수 인코딩 후 이동 한계 불일치")
            if abs(encoded-target) > EPSILON:
                reason.append("encoder_quantization")
            if i < 5 and max(gaps[i], abs(encoded-measured[i])) > WARNING_GAP:
                reason.append("tracking_gap_hold")
            reasons[name] = reason
            clamped.append(target)
        held = [name for name, reason in reasons.items() if "tracking_gap_hold" in reason]
        return dict(raw_action=action, target=clamped, planned_wire_raw=raw,
                    measured_state=measured, active_tracking_gap=gaps, clamp_reasons=reasons,
                    warning_joints=held, status="HELD" if held else "READY", dt=dt)

    def sent(self, raw, now):
        self.previous = self.decode(raw)
        self.previous_raw = dict(raw)
        self.last_sent = now
        self.commands.append(self.previous)
        self.elbow_commands.append(self.previous[ELBOW])
        if self.elbow_excited_at is None and max(self.elbow_commands) - min(self.elbow_commands) >= 1.:
            self.elbow_excited_at = now

    def ratios(self):
        def span(rows, index):
            values = [row[index] for row in rows]
            return max(values) - min(values)
        result = {}
        for i, name in enumerate(JOINTS):
            commanded = span(self.commands, i)
            observed = span(self.observations, i)
            result[name] = dict(commanded_span=commanded, observed_span=observed,
                                ratio=observed/commanded if commanded >= 1. else None)
        return result
