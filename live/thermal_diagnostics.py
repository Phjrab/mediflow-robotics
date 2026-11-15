"""온도 원문과 진단용 필터를 비교한다. 필터는 모터 보호 판정에 사용하지 않는다."""
from collections import deque
import math


RULES = {
    'mode': 'observe_only',
    'jump_gt_c': 10.0,
    'rise_window_s': 1.0,
    'rise_gt_c': 5.0,
    'candidate_stop_c': 55.0,
    'candidate_consecutive': 3,
    'candidate_duration_gt_s': 0.3,
    'candidate_raw_emergency_c': 70.0,
    'candidate_raw_emergency_consecutive': 2,
}


class ThermalDiagnostics:
    """단위·전압 원문을 보존하고, 제안된 필터의 결과만 관찰한다.

    제어용 temperatures를 반환하거나 수정하지 않는다. raw_hold_required는
    기존 55°C 보호와 상태 오류를 설명하는 값이며 필터 결과와 별개다.
    """

    def __init__(self, joints):
        self.joints = tuple(joints)
        self.axes = {}
        self.last = {}
        self.errors = dict(total=0, communication=0, checksum=0, other=0)

    def record_error(self, error):
        observed = getattr(error, 'observed', {}) or {}
        message = str(error)
        checksum = '체크섬' in message or 'checksum' in message.lower()
        communication = observed.get('communication') not in (None, 0)
        communication |= 'timeout' in message.lower() or '통신' in message
        kind = 'checksum' if checksum else 'communication' if communication else 'other'
        self.errors['total'] += 1
        self.errors[kind] += 1
        return {'kind': kind, 'message': message, 'observed': observed,
                'counts': dict(self.errors)}

    def observe_axis(self, joint, now, raw_c, voltage_v, status=0, source_values=()):
        if joint not in self.joints:
            raise ValueError('미확인 관절')
        for name, value in [('time', now), ('temperature', raw_c), ('voltage', voltage_v)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('온도 진단 수치 오류: ' + name)
        if not 0 <= raw_c <= 255 or not 0 <= voltage_v <= 25.5 or type(status) is not int or not 0 <= status <= 255:
            raise ValueError('온도·전압·상태 형식 오류')
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 255
               for value in source_values):
            raise ValueError('온도 원본 출처 값 오류')
        if joint not in self.axes:
            self.axes[joint] = dict(history=deque(), previous=None, filtered=None,
                                    samples=0, spikes=0, raw_peak=None, filtered_peak=None,
                                    hot_count=0, hot_since=None, raw_emergency_count=0)
        axis = self.axes[joint]
        history = axis['history']
        if history and now <= history[-1][0]:
            raise ValueError('관절별 관측 시각이 증가하지 않음')
        while history and now - history[0][0] > RULES['rise_window_s']:
            history.popleft()
        previous = axis['previous']
        delta = None if previous is None else raw_c - previous
        rise = None if not history else raw_c - min(value for _, value in history)
        reasons = []
        if delta is not None and delta > RULES['jump_gt_c']:
            reasons.append('previous_jump_gt_10c')
        if rise is not None and rise > RULES['rise_gt_c']:
            reasons.append('rise_gt_5c_within_1s')
        suspected = bool(reasons)
        if axis['filtered'] is None or not suspected:
            axis['filtered'] = raw_c
        filtered = axis['filtered']
        axis['samples'] += 1
        axis['spikes'] += int(suspected)
        raw_max = max((raw_c, *source_values))
        axis['raw_peak'] = raw_max if axis['raw_peak'] is None else max(axis['raw_peak'], raw_max)
        axis['filtered_peak'] = filtered if axis['filtered_peak'] is None else max(axis['filtered_peak'], filtered)
        axis['previous'] = raw_c
        history.append((now, raw_c))
        axis['raw_emergency_count'] = axis['raw_emergency_count'] + 1 if raw_c >= RULES['candidate_raw_emergency_c'] else 0
        if filtered >= RULES['candidate_stop_c']:
            axis['hot_count'] += 1
            if axis['hot_since'] is None:
                axis['hot_since'] = now
        else:
            axis['hot_count'], axis['hot_since'] = 0, None
        held_for = 0 if axis['hot_since'] is None else now - axis['hot_since']
        candidate = (axis['hot_count'] >= RULES['candidate_consecutive']
                     and held_for > RULES['candidate_duration_gt_s'])
        candidate |= axis['raw_emergency_count'] >= RULES['candidate_raw_emergency_consecutive']
        row = dict(raw_c=raw_c, filtered_c=filtered, suspected_spike=suspected,
                   spike_reasons=reasons, delta_previous_c=delta, rise_within_1s_c=rise,
                   voltage_v=voltage_v, status=status, spike_count=axis['spikes'],
                   raw_peak_c=axis['raw_peak'], filtered_peak_c=axis['filtered_peak'],
                   candidate_filtered_hold=bool(candidate), raw_hold_required=raw_c >= 55 or status != 0,
                   filter_controls_motion=False)
        self.last[joint] = row
        return dict(row)

    def observe(self, now, raw, voltages, statuses, source_values=None):
        expected = set(self.joints)
        if any(set(mapping) != expected for mapping in (raw, voltages, statuses)):
            raise ValueError('온도 진단 6축 키 불일치')
        source_values = source_values or {}
        return {joint: self.observe_axis(joint, now, raw[joint], voltages[joint], statuses[joint],
                                        source_values.get(joint, ()))
                for joint in self.joints}

    def summary(self):
        return {'mode': 'observe_only', 'filter_controls_motion': False, 'rules': dict(RULES),
                'errors': dict(self.errors),
                'joints': {joint: {'samples': a['samples'], 'spike_count': a['spikes'],
                                   'raw_peak_c': a['raw_peak'], 'filtered_peak_c': a['filtered_peak']}
                           for joint, a in self.axes.items()}}
