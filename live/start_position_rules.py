"""Pure position rules shared by the controller and the home web service."""
import math
from config_runtime import load, criteria, command_limits


def start_criteria(limits=None):
    return criteria()


def home_targets(c=None):
    return dict((c or load())['home']['target_ticks'])


def observed_limits(c=None):
    c = c or load()
    margin = c['position_checks']['observed_upper_margin_ticks']
    # Lower limits are unchanged. The approved margin is upper-only.
    return {j: (lo, min(4095, hi + margin)) for j, (lo, hi) in c['limits'].items()}


def position_check(present_raw, targets=None, c=None):
    c = c or load()
    targets = home_targets(c) if targets is None else targets
    observed = observed_limits(c)
    rows = {}
    for j in c['joint_names']:
        value = present_raw.get(j)
        lo, hi = command_limits(c)[j]
        valid = type(value) is int and 0 <= value <= 4095
        safe = valid and observed[j][0] <= value <= observed[j][1]
        error = value - targets[j] if valid else None
        grip = j == 'gripper'
        unit_per_tick = 100 / (c['calibration_ranges'][j][1] - c['calibration_ranges'][j][0]) if grip else 360 / 4095
        error_units = error * unit_per_tick if valid else None
        converged = valid and (abs(error_units) <= c['position_checks']['gripper_tolerance_points']
                              if grip else abs(error) <= c['position_checks']['home_tolerance_ticks'])
        rows[j] = dict(present_ticks=value if valid else None, target_ticks=targets[j],
                       command_range_ticks=[lo, hi], observed_range_ticks=list(observed[j]),
                       valid=valid, safe=safe, above_command_upper=bool(valid and value > hi),
                       residual_ticks=error, residual_units=error_units,
                       unit='point' if grip else 'degree', converged=bool(converged))
    return dict(safe=all(r['safe'] for r in rows.values()),
                home_converged=all(r['converged'] for r in rows.values()), joints=rows,
                warnings=[j for j, r in rows.items() if r['safe'] and r['above_command_upper']],
                blocked_joints=[j for j, r in rows.items() if not r['safe']])


def validate_position(present_raw, limits=None):
    result = position_check(present_raw)
    if not result['safe']:
        import json
        bad = {j: result['joints'][j] for j in result['blocked_joints']}
        raise ValueError('실제 위치 안전 범위 이탈: ' + json.dumps(bad, ensure_ascii=False))
    return result


class HomeConvergence:
    """Require fresh samples inside the residual tolerance for 0.5 seconds."""
    def __init__(self, targets=None, c=None):
        self.config = c or load()
        self.targets = targets
        self.since = self.previous = None

    def observe(self, raw, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('홈 표본 시간 형식 오류')
        result = position_check(raw, self.targets, self.config)
        gap = self.config['position_checks']['max_sample_gap_s']
        if self.previous is not None and (now <= self.previous or now - self.previous > gap):
            self.since = None
        self.previous = now
        if result['safe'] and result['home_converged']:
            if self.since is None:
                self.since = now
        else:
            self.since = None
        stable = 0. if self.since is None else max(0., now - self.since)
        result.update(stable_seconds=stable,
                      ready=bool(self.since is not None and stable + 1e-9 >= self.config['position_checks']['stable_s']))
        return result


def assess_history(samples, c=None):
    if not isinstance(samples, list) or not 1 <= len(samples) <= 64:
        raise ValueError('홈 표본은 1~64개 필요')
    window = HomeConvergence(c=c)
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get('present_raw'), dict):
            raise ValueError('홈 표본 위치 형식 오류')
        result = window.observe(sample['present_raw'], sample.get('time_s'))
    return result
