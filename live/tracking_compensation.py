"""Bounded, leaky position-goal compensation. No bus or configuration writes."""
import math


class TrackingCompensator:
    def __init__(self, joints, settings, *, bounds=None):
        self.joints = tuple(joints)
        self.settings = dict(settings)
        self.enabled = set(settings.get('enabled_axes', ()))
        if not self.enabled <= set(self.joints[:5]):
            raise ValueError('그리퍼·미확인 축 추종 보상 금지')
        self.bounds = bounds
        self.offsets = [0.] * len(self.joints)
        self.ages = [0.] * len(self.joints)
        self.speeds = [0.] * len(self.joints)
        self.peaks = [0.] * len(self.joints)
        self.previous = None
        self.previous_errors = None
        self.last = None
        self.steps = 0
        # Reserve one motor tick so SDK integer conversion stays within 3 degrees.
        cap = float(settings.get('max_offset_deg', 3.))
        if not 0 < cap <= 3:
            raise ValueError('추종 보상 상한 3도 초과')
        self.cap = max(0., cap - 360 / 4095)

    def apply(self, reference, position, now):
        n = len(self.joints)
        if (len(reference) != n or len(position) != n
                or not all(math.isfinite(v) for v in (*reference, *position, now))):
            raise ValueError('추종 보상 입력 형식 오류')
        errors = [a-b for a, b in zip(reference, position)]
        elapsed = 0. if self.last is None else now-self.last
        if elapsed < 0:
            raise ValueError('추종 보상 시계 역행')
        dt = min(elapsed, .1)
        gap = elapsed > self.settings.get('reset_gap_s', .25)
        result, reasons = list(reference), []
        for i, joint in enumerate(self.joints):
            if joint not in self.enabled:
                self.offsets[i] = 0.
                reasons.append('disabled')
                continue
            error = errors[i]
            if self.previous is not None and dt:
                instantaneous = abs(reference[i]-self.previous[i]) / max(elapsed, 1e-9)
                weight = 1-math.exp(-elapsed/.15)
                self.speeds[i] += weight*(instantaneous-self.speeds[i])
            consistent = self.previous_errors is None or error*self.previous_errors[i] > 0
            eligible = (abs(error) > self.settings['error_deadband_deg'] and consistent
                        and self.speeds[i] <= self.settings['max_reference_speed_deg_s'] and not gap)
            self.ages[i] = self.ages[i]+dt if eligible else 0.
            previous = self.offsets[i]
            candidate = previous*math.exp(-self.settings['decay_per_s']*elapsed)
            reason = 'decay'
            if previous*error < 0:
                # Do not accumulate against a reversed error. Unwind smoothly.
                step = self.settings['max_accumulation_deg_s']*dt
                candidate = math.copysign(max(0., abs(candidate)-step), candidate)
                reason = 'reverse_decay'
            elif self.ages[i] >= self.settings['settle_s']:
                rate = self.settings['integral_gain_per_s']*error
                rate = max(-self.settings['max_accumulation_deg_s'],
                           min(self.settings['max_accumulation_deg_s'], rate))
                candidate += rate*dt
                reason = 'accumulate'
            # Bound both the update rate and the accumulated offset.
            slew = self.settings['max_accumulation_deg_s']*dt
            candidate = max(previous-slew, min(previous+slew, candidate))
            candidate = max(-self.cap, min(self.cap, candidate))
            if self.bounds is not None:
                low, high = self.bounds[i]
                bounded = max(low-reference[i], min(high-reference[i], candidate))
                if bounded != candidate:
                    reason = 'range_antiwindup'
                candidate = bounded
            self.offsets[i] = candidate
            self.peaks[i] = max(self.peaks[i], abs(candidate))
            result[i] += candidate
            reasons.append(reason)
        self.steps += 1
        self.previous, self.previous_errors, self.last = list(reference), errors, now
        return result, dict(reference=list(reference), error_deg=errors,
                            offset_deg=list(self.offsets), reason=reasons,
                            reference_speed_deg_s=list(self.speeds))

    def summary(self):
        return dict(enabled_axes=sorted(self.enabled), steps=self.steps,
                    requested_cap_deg=self.settings.get('max_offset_deg', 3.),
                    effective_cap_deg=self.cap,
                    max_abs_offset_deg=dict(zip(self.joints, self.peaks)),
                    final_offset_deg=dict(zip(self.joints, self.offsets)))
