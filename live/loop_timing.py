"""READY-relative control timing; asynchronous inference is not loop work."""
import math
import statistics
import time


def distribution(values):
    values = sorted(v for v in values if v is not None and math.isfinite(v))
    def percentile(p):
        if not values: return None
        index = (len(values)-1)*p
        lo, hi = math.floor(index), math.ceil(index)
        return values[lo]+(values[hi]-values[lo])*(index-lo)
    return dict(count=len(values), mean=statistics.mean(values) if values else None,
                p5=percentile(.05), p95=percentile(.95),
                min=min(values, default=None), max=max(values, default=None))


class LoopSample:
    def __init__(self, owner, started, phase):
        self.owner, self.started, self.phase = owner, started, phase
        self.durations = {}
        self.inference_ms = None
        self.action_starved = False
        self.inference_pending = False
        self.camera_missing = False
        self.finished = False
        self.period = None if owner.previous_start is None else started-owner.previous_start
        owner.previous_start = started

    def call(self, name, function, *args, **kwargs):
        start = self.owner.clock()
        try: return function(*args, **kwargs)
        finally:
            self.durations[name] = self.durations.get(name, 0.)+max(0., self.owner.clock()-start)

    def finish(self, *, phase=None, selected_phase=None, failed=False):
        if self.finished: return
        self.finished = True
        end = self.owner.clock()
        row = dict(event='loop_timing', time_s=end-self.owner.started,
                   monotonic_s=end, loop_start_monotonic_s=self.started, unix=time.time(),
                   loop_start_s=self.started-self.owner.started,
                   loop_period_ms=None if self.period is None else self.period*1000,
                   loop_elapsed_ms=max(0., end-self.started)*1000,
                   cadence_wait_ms=self.durations.get('cadence_wait', 0.)*1000,
                   loop_work_ms=max(0., end-self.started-self.durations.get('cadence_wait', 0.))*1000,
                   read_ms=self.durations.get('serial_read', 0.)*1000,
                   write_ms=self.durations.get('serial_write', 0.)*1000,
                   camera_ms=self.durations.get('camera', 0.)*1000,
                   recorder_enqueue_ms=self.durations.get('recorder_enqueue', 0.)*1000,
                   preview_enqueue_ms=self.durations.get('preview_enqueue', 0.)*1000,
                   inference_blocking_ms=self.durations.get('inference_blocking', 0.)*1000,
                   inference_ms=self.inference_ms,
                   action_index=phase, selected_action_index=selected_phase,
                   action_starved=self.action_starved, inference_pending=self.inference_pending,
                   camera_missing=self.camera_missing, failed=failed)
        measured = sum(row[k] for k in ('read_ms','write_ms','camera_ms','recorder_enqueue_ms',
                                       'preview_enqueue_ms','inference_blocking_ms'))
        row['other_work_ms'] = max(0., row['loop_work_ms']-measured)
        self.owner.rows.append(row)
        if phase is not None: self.owner.phases.append((row['time_s'], phase))
        self.owner.log(row)


class LoopTiming:
    def __init__(self, started, speed, log, *, clock=time.monotonic, policy_timeline=True):
        self.started, self.speed, self.log, self.clock = started, speed, log, clock
        self.policy_timeline = policy_timeline
        self.previous_start = None
        self.rows, self.inferences, self.phases = [], [], [(0., 0.)]

    def begin(self, started=None, phase=None):
        return LoopSample(self, self.clock() if started is None else started, phase)

    def inference(self, seconds, at=None):
        self.inferences.append((self.clock()-self.started if at is None else at, seconds*1000))

    def _phase_at(self, moment):
        # Piecewise linear estimate between transmitted action-index samples;
        # after the last successful transmission the timeline remains held.
        previous = self.phases[0]
        for point in self.phases[1:]:
            if point[0] >= moment:
                width = point[0]-previous[0]
                return point[1] if width <= 0 else previous[1]+(point[1]-previous[1])*(moment-previous[0])/width
            previous = point
        return previous[1]

    def summary(self, ended=None):
        elapsed = max(0., (self.clock() if ended is None else ended)-self.started)
        windows = []
        for index in range(math.ceil(elapsed)):
            start, end = float(index), min(elapsed, index+1.)
            rows = [r for r in self.rows if start <= r['time_s'] < end
                    or (end == elapsed and r['time_s'] == end)]
            rates = [1000/r['loop_period_ms'] for r in rows if r['loop_period_ms'] and r['loop_period_ms'] > 0]
            hz = distribution(rates)
            progress = ((self._phase_at(end)-self._phase_at(start))/(end-start)/(30*self.speed)
                        if self.policy_timeline and end > start else None)
            inference = distribution([value for at,value in self.inferences if start <= at < end])
            costs = {label: sum(row[field] for row in rows) for label,field in
                     (('serial_read','read_ms'),('serial_write','write_ms'),('camera','camera_ms'),
                      ('jpeg_enqueue_copy','recorder_enqueue_ms'),('preview_enqueue','preview_enqueue_ms'),
                      ('inference_blocking','inference_blocking_ms'),('other','other_work_ms'))}
            lag = ((hz['p5'] is not None and hz['p5'] < 25)
                   or (progress is not None and progress < .8))
            causes = []
            if lag and costs:
                largest = max(costs, key=costs.get)
                if costs[largest] > 0: causes.append('largest_measured_work:'+largest)
                if any(row['action_starved'] for row in rows): causes.append('action_buffer_empty')
                if any(row['action_starved'] and row['inference_pending'] for row in rows):
                    causes.append('waiting_for_async_inference')
                if any(row['action_starved'] and row['camera_missing'] for row in rows):
                    causes.append('waiting_for_camera')
            windows.append(dict(start_s=start,end_s=end,duration_s=end-start,loops=len(rows),
                                control_hz=hz,inference_ms=inference,action_progress_ratio=progress,
                                action_progress_status='MEASURED' if self.policy_timeline else 'NOT_APPLICABLE_NO_POLICY',
                                lag=lag, label='루프 밀림' if lag else '정상' if rows else '미측정',
                                measured_work_ms=costs,possible_causes=causes,
                                cadence_wait_ms=sum(row['cadence_wait_ms'] for row in rows),
                                max_loop_work_ms=max((row['loop_work_ms'] for row in rows), default=None),
                                note='Async inference and child JPEG CPU overlap; not additive motor-loop time.'))
        return dict(window_seconds=1.,elapsed_s=elapsed,windows=windows,
                    lag_windows=sum(row['lag'] for row in windows),
                    thresholds=dict(control_p5_hz=25, action_progress_ratio=.8),
                    window_assignment='Loop completion timestamp; long calls assigned to completion window',
                    read_scope='io.checked includes parsing, safety checks, and synchronous packet logging',
                    period_basis='loop start to previous loop start; first period unavailable',
                    attribution='Measured work identifies candidates, not proof of scheduling causality')


def terminal_lines(summary):
    def value(number, suffix=''):
        return '미측정' if number is None else f'{number:.2f}{suffix}'
    result = []
    for row in (summary or {}).get('windows', []):
        hz, inference = row['control_hz'], row['inference_ms']
        progress = row['action_progress_ratio']
        result.append(f"타이밍 {row['start_s']:.0f}~{row['end_s']:.2f}s {row['label']} "
                      f"Hz 평균/p5/최소 {value(hz['mean'])}/{value(hz['p5'])}/{value(hz['min'])} "
                      f"추론ms 평균/최대 {value(inference['mean'])}/{value(inference['max'])} "
                      f"진행률 {value(progress) if progress is not None else '해당없음'} "
                      f"소요ms {row['measured_work_ms']}")
    return result
