"""Protection from fresh individual temperature samples over a time window."""
from collections import deque
import math
import statistics


class ThermalWindow:
    def __init__(self, joints, settings):
        self.joints = tuple(joints)
        self.window = float(settings['window_s'])
        self.continuous = float(settings['continuous_hot_s'])
        self.stop_c = float(settings['stop_c'])
        self.warning_c = float(settings['median_warning_c'])
        self.max_gap = float(settings['max_sample_gap_s'])
        self.axes = {}

    def observe_axis(self, joint, now, value):
        if joint not in self.joints or not all(math.isfinite(v) for v in (now, value)) or not 0 <= value <= 255:
            raise ValueError('온도 창 입력 형식 오류')
        a = self.axes.setdefault(joint, dict(history=deque(), started=now, last=None,
            hot_since=None, hot_samples=0, hot_peak=None, spike_count=0,
            warning=False, warnings=0, hold=False, raw_peak=0, median_peak=None))
        if a['last'] is not None and now <= a['last']:
            raise ValueError('온도 표본 시각 역행 또는 중복')
        gap = a['last'] is not None and now-a['last'] > self.max_gap
        if gap:
            a['history'].clear()
            a['started'], a['hot_since'], a['hot_samples'] = now, None, 0
        ended_spike = None
        if value >= self.stop_c:
            if a['hot_since'] is None:
                a['hot_since'], a['hot_peak'], a['hot_samples'] = now, value, 0
            a['hot_samples'] += 1
            a['hot_peak'] = max(a['hot_peak'], value)
        elif a['hot_since'] is not None:
            if not a['hold']:
                a['spike_count'] += 1
                ended_spike = dict(samples=a['hot_samples'], peak_c=a['hot_peak'],
                                   duration_s=now-a['hot_since'])
            a['hot_since'], a['hot_samples'] = None, 0
        a['history'].append((now, value))
        cutoff = now-self.window
        while a['history'] and a['history'][0][0] < cutoff-1e-9:
            a['history'].popleft()
        values = [v for _,v in a['history']]
        ready = now-a['started'] >= self.window-1e-9
        median = statistics.median(values) if values else None
        continuous_s = 0. if a['hot_since'] is None else now-a['hot_since']
        continuous_hold = continuous_s >= self.continuous-1e-9
        median_hold = ready and median >= self.stop_c
        hold = bool(continuous_hold or median_hold)
        warning = bool(ready and median >= self.warning_c)
        warning_started = warning and not a['warning']
        if warning_started:
            a['warnings'] += 1
        a['warning'], a['hold'], a['last'] = warning, hold, now
        a['raw_peak'] = max(a['raw_peak'], value)
        if ready:
            a['median_peak'] = median if a['median_peak'] is None else max(a['median_peak'],median)
        return dict(source='fresh_individual_20ms', raw_c=value, observed_monotonic_s=now,
                    window_s=self.window, window_ready=ready, samples=len(values), median_c=median,
                    continuous_hot_s=continuous_s, warning=warning, warning_started=warning_started,
                    hold=hold, reason='continuous_hot_2s' if continuous_hold else 'median_2s' if median_hold else None,
                    ended_spike=ended_spike, spike_count=a['spike_count'], sample_gap_reset=gap)

    def summary(self):
        return dict(window_s=self.window, continuous_hot_s=self.continuous,
                    stop_c=self.stop_c, median_warning_c=self.warning_c,
                    joints={j:dict(spike_count=a['spike_count'], warning_count=a['warnings'],
                                   raw_peak_c=a['raw_peak'], median_peak_c=a['median_peak'])
                            for j,a in self.axes.items()})
