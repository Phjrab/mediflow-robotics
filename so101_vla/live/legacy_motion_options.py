"""Optional legacy grasp approach and data-derived wire slew limit."""
import json
import math
from dataclasses import dataclass
from pathlib import Path

from direct_live import JOINTS


REFERENCE = Path(__file__).with_name('assets') / 'grasp_motion_reference.json'
ASSIST_LABEL = 'VLA + 집기 보조'
NEAR_DEG = 5.
RAMP_S = .30


@dataclass(frozen=True)
class MotionOptions:
    assist_grasp: bool
    close_median: tuple
    rates: tuple

    def metadata(self):
        return dict(run_label=ASSIST_LABEL if self.assist_grasp else 'VLA',
                    assist_grasp=self.assist_grasp, vel_limit='data' if self.rates else None,
                    close_median=list(self.close_median), limit_per_s=list(self.rates))


def load_options(assist_grasp, vel_limit, speed):
    if vel_limit not in (None, 'data'):
        raise ValueError('legacy vel_limit must be None or data')
    if not assist_grasp and vel_limit is None:
        return None
    data = json.loads(REFERENCE.read_text(encoding='utf-8'))
    if data['joint_names'] != list(JOINTS):
        raise ValueError('training reference joint order mismatch')

    def vector(key):
        values = data[key]
        if len(values) != 6 or not all(type(x) in (int, float) and math.isfinite(x) for x in values):
            raise ValueError(f'invalid training reference {key}')
        return tuple(values)

    rates = ()
    if vel_limit == 'data':
        p99 = vector('action_p99_per_s')
        reference_speed = data.get('action_p99_reference_speed', data.get('speed', 1.))
        if not all(math.isfinite(x) and x > 0 for x in (*p99, reference_speed, speed)):
            raise ValueError('invalid reference or playback rate')
        rates = tuple(value * speed / reference_speed for value in p99)
    return MotionOptions(assist_grasp, vector('close_median'), rates)


class GraspAssist:
    """One short approach per policy close. Gripper requests are only held or passed."""
    def __init__(self, median):
        self.median = median
        self.started = self.held_points = self.held_tick = self.previous = None
        self.trough = None
        self.windows = self.steps = 0

    def apply(self, model, knot, sample, now):
        state = sample['state']
        grip = model[-1]
        rising = self.previous is not None and grip > self.previous
        self.previous = grip
        near = all(abs(p-m) <= NEAR_DEG for row in (model, knot, state)
                   for p, m in zip(row[:5], self.median))
        closing = not rising and grip < state[-1] and knot[-1] < state[-1]
        if self.trough is not None:
            self.trough = min(self.trough, grip)
            if grip >= self.trough + 3.:
                self.trough = None
        if self.started is not None and (not near or rising or grip > state[-1] or knot[-1] > state[-1]
                                         or grip >= self.held_points
                                         or now-self.started >= 2*RAMP_S):
            self.started = None
        if (self.started is None and self.trough is None and near and closing
                and state[-1] >= self.median[-1] and state[-1]-grip >= 1.):
            self.started = now
            self.held_points = state[-1]
            self.held_tick = sample['present_raw']['gripper']
            self.trough = grip
            self.windows += 1
        offset = [0.] * 5
        holding = False
        if self.started is not None:
            elapsed = now-self.started
            weight = max(0., min(1., elapsed/RAMP_S, (2*RAMP_S-elapsed)/RAMP_S))
            weight = weight*weight*(3.-2.*weight)
            offset = [(m-p)*weight for m, p in zip(self.median[:5], model)]
            holding = elapsed < RAMP_S
            self.steps += any(offset)
        return dict(offset_deg=offset, holding=holding, near=near, policy_closing=closing)

    @property
    def release_at(self):
        return self.started + RAMP_S


class WireRateLimit:
    """Clip the already guarded integer packet, measured from the last sent Goal."""
    def __init__(self, io, rates):
        self.bounds = io.raw_bounds
        self.normalize = io.normalize
        low = {j: self.bounds[j][0] for j in JOINTS}
        high = {j: self.bounds[j][1] for j in JOINTS}
        spans = zip(JOINTS, self.normalize(low), self.normalize(high), rates)
        self.ticks_per_s = {j: rate*(high[j]-low[j])/abs(b-a) for j, a, b, rate in spans}
        self.remainder = dict.fromkeys(JOINTS, 0.)
        self.direction = dict.fromkeys(JOINTS, 0)

    def apply(self, plan, sample, previous, dt, *, protect_gripper):
        requested = plan['wire_raw'].copy()
        previous = previous if previous is not None else sample['present_raw']
        limited = []
        feedback_hold = False
        for joint in JOINTS:
            if protect_gripper and joint == 'gripper':
                self.remainder[joint] = 0.
                continue
            lo, hi = self.bounds[joint]
            anchor = min(hi, max(lo, previous[joint]))
            delta = requested[joint] - anchor
            direction = (delta > 0) - (delta < 0)
            budget = self.ticks_per_s[joint]*dt
            # Only accumulate a sub-tick budget. Never bank idle or stalled time.
            sub_tick = budget < 1
            if sub_tick and direction == self.direction[joint]:
                budget += self.remainder[joint]
            self.direction[joint] = direction
            steps = min(abs(delta), math.floor(budget))
            self.remainder[joint] = budget-steps if sub_tick and steps < abs(delta) else 0.
            value = anchor + direction*steps
            if joint == 'gripper':
                # A slowed reversal must not issue an opposing grip demand.
                measured = min(hi, max(lo, sample['present_raw'][joint]))
                if (value-measured)*(requested[joint]-measured) <= 0 and value != requested[joint]:
                    feedback_hold = value != measured
                    value = measured
            plan['wire_raw'][joint] = value
            if value != requested[joint]:
                limited.append(joint)
        plan['wire_normalized'] = self.normalize(plan['wire_raw'])
        return dict(before_raw=requested, limited_joints=limited, dt_s=dt,
                    gripper_protection_priority=protect_gripper, gripper_feedback_hold=feedback_hold)
