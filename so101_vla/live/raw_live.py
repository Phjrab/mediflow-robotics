"""Unscaled FIFO policy actions; legacy stream_live.py is deliberately unchanged.

Only the action scheduler is new. The deployed guarded motor IO, thermal policy,
home preparation, gripper protection and HOLD acknowledgement remain shared.
"""
import concurrent.futures
import hashlib
import json
import math
import numbers
import statistics
import time
from collections import deque
from pathlib import Path

import direct_live as base
import live_start
import stream_live as legacy
from grasp_offset import GraspOffset
from grasp_recorder import GraspRecorder
from gripper_safety import OpeningGuard
from loop_timing import LoopTiming, distribution
from vla_monitor import Monitor


ROOT = Path(__file__).resolve().parents[1]


def load_reference(path=None, *, verify_metadata=True):
    """Derive FPS from all six actual source metadata files, never a CLI guess."""
    path = Path(path) if path is not None else ROOT / 'analysis/raw_action_reference.json'
    reference = json.loads(path.read_text(encoding='utf-8-sig'))
    fps = reference['fps']
    if type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('raw dataset fps invalid')
    if set(reference['raw_guard_1_5']) != set(base.JOINTS):
        raise ValueError('raw action jump reference joint mismatch')
    if any(not math.isfinite(v) or v <= 0 for v in reference['raw_guard_1_5'].values()):
        raise ValueError('raw action jump reference invalid')
    if verify_metadata:
        metadata = reference['source_metadata']
        if len(metadata) != 6:
            raise ValueError('raw requires six verified training sources')
        observed_fps = []
        for source in metadata:
            data = Path(source['path']).expanduser().read_bytes()
            if hashlib.sha256(data).hexdigest() != source['sha256']:
                raise ValueError('raw training metadata hash mismatch: ' + source['path'])
            info = json.loads(data)
            if info['features']['action']['names'] != [j + '.pos' for j in base.JOINTS]:
                raise ValueError('raw training joint order mismatch: ' + source['path'])
            observed_fps.append(info['fps'])
            if info['fps'] != source['fps'] or info['fps'] != fps:
                raise ValueError('raw training fps mismatch: ' + source['path'])
        reference['fps'] = observed_fps[0]
    return reference


def _vector(values):
    result = list(values)
    if len(result) != len(base.JOINTS) or any(
            isinstance(v, bool) or not isinstance(v, numbers.Real) or not math.isfinite(v)
            for v in result):
        raise base.Stop('raw action must contain six finite numeric values')
    return [float(v) for v in result]


class AbsoluteDeadline:
    """Skip elapsed wall-clock slots, never burst or discard policy actions."""
    def __init__(self, fps, started):
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('positive finite fps required')
        self.period = 1.0 / fps
        self.next = float(started)
        self.skipped = 0

    def begin(self, now):
        skipped = max(0, math.floor((now - self.next) / self.period + 1e-9))
        due = self.next + skipped * self.period
        self.next = due + self.period
        self.skipped += skipped
        return dict(deadline=due, skipped=skipped, lateness_s=max(0., now - due))


class FifoChunkCursor:
    """One active chunk plus one prefetched chunk; no overlap or prefix skip."""
    def __init__(self, n_action_steps=50):
        if type(n_action_steps) is not int or n_action_steps <= 0:
            raise ValueError('n_action_steps must be a positive integer')
        self.n_action_steps = n_action_steps
        self.active = deque()
        self.pending = None
        self.observed_at = 0.
        self.index = 0
        self.primed = False

    def _chunk(self, actions):
        rows = [_vector(row) for row in actions]
        if len(rows) < self.n_action_steps:
            raise base.Stop('raw policy chunk shorter than checkpoint n_action_steps')
        return deque(rows[:self.n_action_steps])

    @property
    def remaining(self):
        return len(self.active)

    @property
    def has_pending(self):
        return self.pending is not None

    def prime(self, actions, observed_at=0.):
        if self.primed:
            raise RuntimeError('raw FIFO already primed')
        self.active = self._chunk(actions)
        self.observed_at = observed_at
        self.primed = True

    def stage(self, actions, observed_at):
        if not self.primed or self.pending is not None:
            raise RuntimeError('raw FIFO permits only one pending chunk')
        self.pending = (self._chunk(actions), observed_at)

    def pop(self, now):
        if not self.active and self.pending is not None:
            self.active, self.observed_at = self.pending
            self.pending = None
        if not self.active:
            return None
        index, action = self.index, self.active.popleft()
        self.index += 1
        return index, list(action), max(0., now - self.observed_at)

    def future(self, count):
        rows = list(self.active)
        if self.pending is not None:
            rows += list(self.pending[0])
        return [list(row) for row in rows[:count]]


class JumpGuard:
    """Check both unmodified model output and the final quantized wire target."""
    def __init__(self, limits_by_joint, initial_model, initial_wire):
        if set(limits_by_joint) != set(base.JOINTS):
            raise ValueError('raw jump guard joint mismatch')
        self.limits = dict(limits_by_joint)
        if any(not math.isfinite(v) or v <= 0 for v in self.limits.values()):
            raise ValueError('raw jump guard limit invalid')
        self.model = _vector(initial_model)
        self.wire = _vector(initial_wire)

    def check(self, model, wire):
        model, wire = _vector(model), _vector(wire)
        for kind, previous, target in (('model', self.model, model), ('wire', self.wire, wire)):
            for j, old, value in zip(base.JOINTS, previous, target):
                delta = abs(value - old)
                if delta > self.limits[j] + 1e-9:
                    raise base.Stop('raw 학습 최대 변화 1.5배 초과: ' + json.dumps(dict(
                        kind=kind, joint=j, previous=old, target=value,
                        delta=delta, limit=self.limits[j]), ensure_ascii=False))

    def commit(self, model, wire):
        self.model, self.wire = _vector(model), _vector(wire)


def _timing_totals(timing, fps, count, elapsed, inferences, skipped):
    periods = [r['loop_period_ms'] for r in timing.rows if r['loop_period_ms'] is not None]
    rates = [1000. / p for p in periods if p > 0]
    return dict(control_hz=distribution(rates), period_ms=distribution(periods),
                inference_ms=distribution([s * 1000 for s in inferences]),
                actual_action_hz=count / elapsed if elapsed else 0.,
                action_progress_ratio=count / elapsed / fps if elapsed else 0.,
                skipped_loops=skipped)


def raw_stream(io, policy, log, duration, requested_speed=.33, monitor=None, reference=None):
    reference = load_reference() if reference is None else reference
    fps = float(reference['fps'])
    n_action_steps = getattr(policy, 'n_action_steps', None)
    if type(n_action_steps) is not int or n_action_steps <= 0:
        raise ValueError('raw policy must expose checkpoint n_action_steps')
    clock, pause = getattr(io, 'clock', time.monotonic), getattr(io, 'pause', time.sleep)
    pool = getattr(io, 'pool_factory', concurrent.futures.ThreadPoolExecutor)(max_workers=1)
    fifo = FifoChunkCursor(n_action_steps)
    relief, opening, lateral = legacy.GripRelief(), OpeningGuard(), GraspOffset()
    count = relief_count = 0
    reason = None
    hold = {'status': 'NOT_NEEDED'}
    ready = recorder = recording = timing = scheduler = None
    job = None
    waits, inferences, periods, active_periods, ages = [], [], [], [], []
    observed = [[] for _ in base.JOINTS]
    commands = [[] for _ in base.JOINTS]
    previous_send = starvation = missing_since = None
    previous_policy_send = None
    last_requested_gripper = None
    last_wire_raw = None
    safety_events = []
    started = clock()
    loop_elapsed = 0.
    slot = {}
    terminal_cursor = 0
    result_timing = None

    def timing_log(row):
        if row.get('event') == 'loop_timing':
            row.update(mode='raw', fps=fps, speed=1.0,
                       deadline_s=slot.get('deadline', started) - started,
                       deadline_exceeded=clock() > slot.get('deadline', started) + 1 / fps,
                       skipped_time_slots=slot.get('skipped', 0),
                       skipped_loops=scheduler.skipped if scheduler else 0,
                       inference_wait_ms=(row.get('loop_period_ms') or 0.)
                       if row['action_starved'] and row['inference_pending'] else 0.)
        log(row)

    try:
        prime_started = clock()
        actions, seconds, prime_seconds = legacy.prime_policy(io, policy, pool, log, monitor)
        fifo.prime(actions, prime_started)
        initial = io.checked()
        # The first policy action is checked against the measured post-home pose.
        guard = JumpGuard(reference['raw_guard_1_5'], initial['state'], initial['state'])
        inferences.append(seconds)
        estimate = max(seconds, getattr(policy, 'warm_rtc_estimate', seconds))
        if getattr(io, 'out', None) is not None:
            recorder = GraspRecorder(io.out, base.JOINTS, clock=clock)
        started = clock()
        end = started + duration
        scheduler = AbsoluteDeadline(fps, started)
        timing = LoopTiming(started, fps / 30., timing_log, clock=clock)
        preview_due = started
        ready = dict(event='ready', unix=time.time(), monotonic_s=started, mode='raw',
                     fps=fps, speed=1.0, requested_speed=requested_speed,
                     home_speed=requested_speed, duration_s=duration,
                     first_inference_s=seconds, first_observation_and_inference_s=prime_seconds)
        print('READY ' + json.dumps(ready, ensure_ascii=False), flush=True)
        log(ready)
        log(dict(event='inference', origin=0, time_s=0., seconds=seconds,
                 incoming_actions=actions, skipped_actions=0, overlap=0, primed_before_ready=True))
        while True:
            pause(max(0., min(end, scheduler.next) - clock()))
            now = clock()
            if now >= end:
                break
            slot = scheduler.begin(now)
            tick = timing.begin(now, count - 1 if count else None)
            failed = True
            try:
                if monitor is not None and monitor.hold_requested():
                    raise base.Stop(legacy.VIEWER_STOP)
                sample = tick.call('serial_read', io.checked)
                state_observed_at = clock()
                for history, value in zip(observed, sample['state']):
                    history.append(value)
                if recorder is not None and recorder.due(clock()):
                    captured = clock()
                    frames = tick.call('camera', io.camera_frames) if hasattr(io, 'camera_frames') else None
                    tick.call('recorder_enqueue', recorder.capture, frames, sample,
                              time_s=captured - started, monotonic=captured)
                if monitor is not None and now >= preview_due:
                    frames = tick.call('camera', io.camera_frames) if hasattr(io, 'camera_frames') else None
                    if frames is not None:
                        tick.call('preview_enqueue', monitor.capture, frames, sample['state'])
                    preview_due = now + .2
                if job is not None and job.done():
                    actions, seconds = tick.call('inference_blocking', job.result)
                    fifo.stage(actions, requested)
                    inferences.append(seconds)
                    estimate = max(seconds, estimate * .85)
                    timing.inference(seconds)
                    tick.inference_ms = seconds * 1000
                    log(dict(event='inference', origin=request_origin, seconds=seconds,
                             time_s=clock() - started, incoming_actions=actions,
                             skipped_actions=0, overlap=0, delivery_observation_age_s=clock() - requested,
                             scheduling='fresh_observation_after_chunk_exhausted'))
                    job = None
                if job is not None and now - requested > 9:
                    raise base.Stop('정책 추론 응답 시간 초과')
                # RTC is disabled for raw. Its ordinary prediction starts at the
                # observation time: an origin number does NOT condition the model
                # on a future state. Prefetching at the start of the current chunk
                # and appending all 50 new actions therefore replays an old pose.
                # Execute the complete unchanged chunk, then observe and infer.
                # While inference is pending, this loop keeps checking safety at
                # dataset FPS and sends no duplicate/extra goals.
                if job is None and not fifo.has_pending and fifo.remaining == 0:
                    frames = tick.call('camera', io.fresh_frames)
                    if frames is not None:
                        missing_since = None
                        requested = clock()
                        request_origin = fifo.index
                        log(dict(event='inference_requested', time_s=requested - started,
                                 origin=request_origin, remaining_actions=fifo.remaining,
                                 remaining_wall_s=fifo.remaining / fps, estimated_inference_s=estimate,
                                 playback_hz=fps, prefix_skip=False,
                                 scheduling='fresh_observation_after_chunk_exhausted',
                                 observation_state=list(sample['state']),
                                 observation_ticks=sample.get('present_raw'),
                                 state_observation_age_s=requested-state_observed_at,
                                 frame_timestamps={name: frame.get('timestamp') if isinstance(frame, dict) else None
                                                   for name, frame in frames.items()},
                                 previous_sent_action_index=count-1 if count else None))
                        job = tick.call('inference_blocking', pool.submit, policy,
                                        frames, sample['state'], request_origin, 0)
                    elif missing_since is None:
                        missing_since = now
                    elif now - missing_since > 8:
                        raise base.Stop('사용 가능한 카메라 영상 없음: 프레임 지연 또는 너무 어두운 영상')
                selected = fifo.pop(now)
                if selected is None:
                    if starvation is None:
                        starvation = now
                    # No policy goal is repeated while inference runs. Gripper
                    # protection nevertheless uses elapsed physical seconds in
                    # raw (speed=1), including the inference wait. Only an actual
                    # safety change gets one new goal; the other five integers
                    # stay identical and the policy cursor does not advance.
                    if last_wire_raw is not None:
                        protective = list(guard.wire)
                        relieved_target = relief.apply(last_requested_gripper, sample['state'][-1],
                                                       clock()-started)
                        protective[-1] = opening.apply(min(relieved_target, opening.cap),
                                                        sample['state'][-1],
                                                        sample.get('loads', {}).get('gripper', 0), now)
                        safety_plan = io.plan(protective)
                        safety_raw = dict(last_wire_raw)
                        safety_raw['gripper'] = safety_plan['wire_raw']['gripper']
                        safety_wire = list(guard.wire)
                        safety_wire[-1] = safety_plan['wire_normalized'][-1]
                        if safety_raw['gripper'] != last_wire_raw['gripper']:
                            guard.check(guard.model, safety_wire)
                            tick.call('cadence_wait', pause,
                                      max(0., min(end, previous_send + 1/fps)-clock()))
                            if clock() >= end:
                                failed = False
                                break
                            if monitor is not None and monitor.hold_requested():
                                raise base.Stop(legacy.VIEWER_STOP)
                            tick.call('serial_write', io.send, safety_raw)
                            previous_send = clock()
                            guard.commit(guard.model, safety_wire)
                            last_wire_raw = safety_raw
                            event = dict(event='gripper_safety_goal', step=count,
                                         action_index=count-1, time_s=previous_send-started,
                                         during='inference_or_camera_wait', policy_index_advanced=False,
                                         reason='opening_load_freeze' if opening.fixed is not None else 'closing_relief',
                                         wire_raw=safety_raw, wire_normalized=safety_wire,
                                         requested_gripper=last_requested_gripper,
                                         present_gripper=sample['state'][-1],
                                         load_raw=sample.get('loads', {}).get('gripper', 0))
                            safety_events.append(event)
                            log(event)
                            relief_count += int(relieved_target != last_requested_gripper)
                    tick.action_starved = True
                    if monitor is not None:
                        tick.call('preview_enqueue', monitor.status, 'predicting', step=count, sample=sample)
                    failed = False
                    continue
                index, model, age = selected
                target, offset = lateral.apply(model, sample['state'], now,
                                                fifo.future(math.ceil(fps)), fps)
                target[-1] = relief.apply(target[-1], sample['state'][-1], clock()-started)
                relieved = target[-1] != model[-1]
                target[-1] = opening.apply(target[-1], sample['state'][-1],
                                            sample.get('loads', {}).get('gripper', 0), now)
                plan = io.plan(target)
                try:
                    guard.check(model, plan['wire_normalized'])
                except base.Stop as exc:
                    log(dict(event='raw_guard_rejected', action_index=index, time_s=clock()-started,
                             reason=str(exc), model=model, wire_normalized=plan['wire_normalized'],
                             last_model=guard.model, last_wire=guard.wire,
                             observation_age_s=age, state=sample['state']))
                    raise
                # A late read must not be followed by two back-to-back writes.
                # Retain the absolute slot schedule and drop elapsed time slots,
                # but also enforce the minimum wire cadence after a delayed send.
                if previous_send is not None:
                    tick.call('cadence_wait', pause,
                              max(0., min(end, previous_send + 1 / fps) - clock()))
                if clock() >= end:
                    failed = False
                    break
                if monitor is not None and monitor.hold_requested():
                    raise base.Stop(legacy.VIEWER_STOP)
                tick.call('serial_write', io.send, plan['wire_raw'])
                sent = clock()
                guard.commit(model, plan['wire_normalized'])
                if previous_policy_send is not None:
                    periods.append(sent - previous_policy_send)
                    if index % n_action_steps != 0:
                        active_periods.append(sent - previous_policy_send)
                previous_send = sent
                previous_policy_send = sent
                last_requested_gripper = model[-1]
                last_wire_raw = dict(plan['wire_raw'])
                if starvation is not None:
                    waits.append(sent - starvation)
                    starvation = None
                ages.append(age)
                count += 1
                relief_count += int(relieved)
                for history, value in zip(commands, plan['wire_normalized']):
                    history.append(value)
                row = dict(event='goal_transmitted', mode='raw', step=count, time_s=sent - started,
                           action_index=index, model_phase=index, playback_s=index / fps,
                           hz_target=fps, model=model, feedback=sample, grip_relief=relieved,
                           observation_age_s=age, lateral_correction=offset,
                           gripper_opening=dict(cap_clipped=model[-1] > opening.cap,
                                                limited_points=target[-1], load_hold_points=opening.fixed),
                           **plan)
                log(row)
                if recorder is not None:
                    event = tick.call('recorder_enqueue', recorder.goal, plan['wire_normalized'][-1], row,
                                      time_s=sent - started, monotonic=sent)
                    if event is not None:
                        log(dict(event='grasp_event', **event))
                if monitor is not None:
                    tick.call('preview_enqueue', monitor.status, 'moving', step=count,
                              sample=sample, model=model, sent=plan['wire_normalized'],
                              inference_seconds=inferences[-1])
                failed = False
            finally:
                tick.inference_pending = job is not None
                tick.camera_missing = missing_since is not None
                tick.finish(phase=count - 1 if count else None, selected_phase=fifo.index - 1, failed=failed)
                second = int(clock() - started)
                if second > terminal_cursor:
                    rows = [r for r in timing.rows if terminal_cursor <= r['time_s'] < second]
                    hz = distribution([1000 / r['loop_period_ms'] for r in rows
                                       if r['loop_period_ms'] is not None and r['loop_period_ms'] > 0])
                    period = distribution([r['loop_period_ms'] for r in rows])
                    inf = distribution([r['inference_ms'] for r in rows if r['inference_ms'] is not None])
                    log(dict(event='raw_timing_second', start_s=terminal_cursor, end_s=second,
                             control_hz=hz, period_ms=period, inference_ms=inf,
                             skipped_loops=scheduler.skipped))
                    def fmt(value): return 'NA' if value is None else f'{value:.2f}'
                    print(f"raw {second}s Hz 평균/최소 {fmt(hz['mean'])}/{fmt(hz['min'])} "
                          f"주기최대ms {fmt(period['max'])} 추론ms {fmt(inf['mean'])} "
                          f"건너뛴루프 {scheduler.skipped}", flush=True)
                    terminal_cursor = second
    except (KeyboardInterrupt, Exception) as exc:
        reason = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
    finally:
        ended = clock()
        loop_elapsed = max(0., ended - started)
        if starvation is not None:
            waits.append(max(0., ended - starvation))
        if io.goal_attempted:
            hold = base.try_hold(io)
            if hasattr(io, 'confirm_thermal_stop'):
                io.confirm_thermal_stop()
        pool.shutdown(wait=False, cancel_futures=True)
        if recorder is not None:
            recording = recorder.close(reason=reason or 'run_end')
            print('집기 이벤트 ' + str(recording['event_count']) + '회 · 시각(READY 이후 초) ' +
                  ', '.join(f"{e['time_s']:.3f}" for e in recording['events']), flush=True)
        if timing is not None:
            result_timing = timing.summary(ended)
    if not count and reason is None:
        reason = '실행 시간 안에 정책 목표가 전송되지 않음'
    tracking = {}
    for j, sent, actual in zip(base.JOINTS, commands, observed):
        sent_span = max(sent) - min(sent) if sent else 0.
        observed_span = max(actual) - min(actual) if actual else 0.
        tracking[j] = dict(sent_span=sent_span, observed_span=observed_span,
                           span_ratio=observed_span / sent_span if sent_span else None)
    return dict(status='STOPPED' if reason == 'Ctrl+C' else 'HOLD' if reason else 'RUN_COMPLETE',
                reason=reason, hold=hold, mode='raw', fps=fps, speed=1.0,
                requested_speed=requested_speed, home_speed=requested_speed,
                interpolation=False, speed_scaling=False, n_action_steps=n_action_steps,
                inference_scheduling='fresh_observation_after_chunk_exhausted',
                steps=count, step_limit=math.ceil(duration * fps), elapsed_s=loop_elapsed,
                task_success='UNVERIFIED', ready=ready, playback_hz=fps,
                playback_s=count / fps, skipped_loops=scheduler.skipped if scheduler else 0,
                skipped_policy_actions=0, loop_timing=result_timing,
                raw_timing=_timing_totals(timing, fps, count, loop_elapsed, inferences, scheduler.skipped)
                if timing is not None else None,
                inference_s=dict(mean=statistics.mean(inferences) if inferences else None,
                                 samples=inferences),
                chunk_wait_s=dict(max=max(waits, default=0.), sum=sum(waits)),
                observation_age_s=distribution(ages),
                transmission_hz=1 / statistics.mean(periods) if periods else None,
                active_chunk_transmission_hz=1 / statistics.mean(active_periods) if active_periods else None,
                overall_action_hz=count / loop_elapsed if loop_elapsed else 0.,
                active_chunk_period_ms=distribution([gap*1000 for gap in active_periods]),
                final_rate_target=fps, tracking=tracking, grasp_recording=recording,
                gripper_relief_count=relief_count, gripper_opening_protection=opening.summary(),
                gripper_safety_goal_packets=len(safety_events), gripper_safety_events=safety_events,
                lateral_correction=lateral.summary(), camera=io.cameras_stats.summary(),
                gripper_peak_c=io.gripper_peak, peak_temperatures_c=dict(io.peak_temperatures))


def start_policy_session(io, policy, home, log, duration, requested_speed=.33,
                         monitor=None, reference=None):
    summary = live_start.prepare(io, home, log)
    io.motion_ready = True
    if hasattr(io, 'policy_active'):
        io.policy_active = True
    print('시작 직전 ' + json.dumps(summary, ensure_ascii=False), flush=True)
    return raw_stream(io, policy, log, duration, requested_speed, monitor, reference)


def make_raw_policy(context):
    config = json.loads((context['checkpoint'] / 'config.json').read_text())
    steps = config['n_action_steps']
    if type(steps) is not int or not 0 < steps <= config['chunk_size']:
        raise ValueError('checkpoint n_action_steps invalid')
    policy = legacy.make_policy(context)
    policy.n_action_steps = steps
    return policy


def _write_summary(out, result):
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                                allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


def run_dry(duration, requested_speed=.33):
    from startup_simulation import SimulatedArm
    from trial_runtime import static_context

    reference, context = load_reference(), static_context()
    out = ROOT / 'runs' / f'DRY_RAW_{time.time_ns()}'
    out.mkdir()
    result = dict(mode='raw', passed=False, steps=0, status='HOLD', reason=None)
    camera_source = policy = monitor = io = None
    first_targets, arm_errors = [], []
    with (out / 'steps.jsonl').open('x', encoding='utf-8', buffering=1) as trace:
        def log(row):
            trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
            if row.get('event') == 'goal_transmitted' and len(first_targets) < 10:
                first_targets.append(row['wire_raw'])
            if row.get('event') == 'startup_torque_readback':
                arm_errors.extend(row['goal_error_ticks'].values())
        try:
            monitor = Monitor(out.name, 'RAW 카메라·모델 실제 / 모터 모의', base.JOINTS, controls=False)
            monitor.status('warming', force=True)
            policy = make_raw_policy(context)
            camera_source = legacy.device_class()(context, log, out)
            io = SimulatedArm(context['home']['state'], {}, log, 'camera_dry', real_time=True)
            io.out = out
            io.connect()
            camera_source.open_cameras(io.checked)
            io.fresh_frames, io.camera_frames = camera_source.fresh_frames, camera_source.camera_frames
            io.camera_map, io.cameras_stats = camera_source.camera_map, camera_source.cameras_stats
            result = start_policy_session(io, policy, context['home']['state'], log,
                                          duration, requested_speed, monitor, reference)
        except (KeyboardInterrupt, Exception) as exc:
            result.update(status='STOPPED' if isinstance(exc, KeyboardInterrupt) else 'HOLD',
                          reason='Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc))
        finally:
            cleanup = []
            for name, resource in (('camera', camera_source), ('policy', policy)):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        cleanup.append(name + ': ' + str(exc))
            if camera_source is not None:
                cleanup += camera_source.cleanup_errors
            result.update(output=str(out), physical_motor_writes=0, serial_open_blocked=True,
                          first_targets=first_targets, max_arm_error_ticks=max(arm_errors, default=0),
                          camera_cleanup_errors=cleanup,
                          observation_scope=dict(images='actual stationary robot cameras',
                                                 state='simulated follower response',
                                                 physically_closed_loop=False,
                                                 limitation='Camera images do not follow simulated arm motion; this validates software execution, not physical grasp success.'))
            result['functional_passed'] = (result['status'] == 'RUN_COMPLETE' and result['steps'] > 0
                                           and result['elapsed_s'] >= duration - .05 and not cleanup
                                           and result['max_arm_error_ticks'] <= 3)
            measured = result.get('raw_timing') or {}
            p5 = measured.get('control_hz', {}).get('p5')
            result['timing_passed'] = (p5 is not None and p5 >= 25
                                       and measured.get('action_progress_ratio', 0.) >= .8)
            result['passed'] = result['functional_passed'] and result['timing_passed']
            if monitor is not None:
                monitor.finish('run_complete' if result['passed'] else 'hold',
                               result.get('reason'), result['steps'])
            _write_summary(out, result)
    return 0 if result['passed'] else 1


def run_simulations(duration, requested_speed=.33):
    """Actual policy and recorded images, but only in-memory servo writes."""
    import numpy as np
    from PIL import Image
    from startup_simulation import SimulatedArm, SCENARIOS, CAMERAS
    from trial_runtime import static_context

    reference, context = load_reference(), static_context()
    preview = ROOT / 'analysis/vla_v3_blue_eval_preview'
    frames = {name: {'pixels': np.array(Image.open(preview / f'training_{name}.png').convert('RGB'))}
              for name in CAMERAS}
    policy = make_raw_policy(context)
    success = True
    try:
        for case in SCENARIOS:
            errors = []
            def log(row):
                if row.get('event') == 'startup_torque_readback':
                    errors.extend(row['goal_error_ticks'].values())
            io = SimulatedArm(context['home']['state'], frames, log, case)
            policy.reset()
            active_policy = policy
            if case == 'blocked_gripper':
                calls = 0
                class ClosingPolicy:
                    n_action_steps = policy.n_action_steps
                    warm_rtc_estimate = policy.warm_rtc_estimate
                    def __call__(self, *args):
                        nonlocal calls
                        actions, seconds = policy(*args)
                        calls += 1
                        io.gripper_blocked = calls > 1
                        grip = 20. if calls == 1 else 0.
                        return [row[:-1] + [grip] for row in actions], seconds
                active_policy = ClosingPolicy()
            try:
                io.connect()
                result = start_policy_session(io, active_policy, context['home']['state'], log,
                                              duration, requested_speed, reference=reference)
            except (KeyboardInterrupt, Exception) as exc:
                result = dict(status='STOPPED' if isinstance(exc, KeyboardInterrupt) else 'HOLD',
                              reason='Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc), steps=0)
            expected = {'ctrl_c': ('STOPPED', 'Ctrl+C'),
                        'overheat': ('HOLD', '모터 상태코드/온도 중단'),
                        'usb_disconnect': ('HOLD', '팔로워 USB 끊김')}.get(case)
            if case in ('elbow_plus_5', 'elbow_plus_20'):
                passed = (result['status'] == 'HOLD' and result['steps'] == 0
                          and '실제 위치 안전 범위 이탈' in result['reason']
                          and not io.start_writes and io.packet_count == 0)
            elif expected:
                passed = (result['status'] == expected[0] and result['steps'] > 0
                          and (result.get('reason') or '').startswith(expected[1]))
            else:
                passed = result['status'] == 'RUN_COMPLETE' and result['steps'] > 0
            passed &= max(errors, default=0) <= 3
            if case == 'blocked_gripper':
                passed &= result.get('gripper_relief_count', 0) > 0
            result.update(mode='raw', case=case, passed=bool(passed), physical_motor_writes=0,
                          max_arm_error_ticks=max(errors, default=0))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            success &= passed
    finally:
        policy.close()
    return 0 if success else 1


def live(duration, supported_home=False, speed=.33):
    from trial_runtime import static_context
    from run_exit_report import record_run_directory

    reference, context = load_reference(), static_context()
    out = ROOT / 'runs' / (time.strftime('%Y%m%d_%H%M%S') + '_raw_' + str(time.time_ns()))
    out.mkdir()
    record_run_directory(out)
    result = dict(status='HOLD', steps=0, reason=None, task_success='UNVERIFIED',
                  mode='raw', fps=reference['fps'], speed=1.0, requested_speed=speed)
    io = policy = monitor = None
    startup = []
    with (out / 'steps.jsonl').open('x', encoding='utf-8', buffering=1) as trace:
        def log(row):
            trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
            if row.get('event', '').startswith('startup_'):
                startup.append(row)
        try:
            monitor = Monitor(out.name, base.TASK, base.JOINTS)
            monitor.status('warming', force=True)
            log(dict(event='startup_model_begin', mode='raw', unix=time.time()))
            began = time.monotonic()
            policy = make_raw_policy(context)
            log(dict(event='startup_model_complete', seconds=time.monotonic() - began, unix=time.time()))
            monitor.status('connecting', force=True)
            io = legacy.device_class()(context, log, out)
            io.connect()
            log(dict(event='start', mode='raw', fps=reference['fps'], speed=1.0,
                     requested_speed=speed, duration_s=duration, raw_bounds=io.raw_bounds,
                     device_preflight=io.preflight, prior_stop_inventory=context['stop_inventory']))
            result = start_policy_session(io, policy, context['home']['state'], log,
                                          duration, speed, monitor, reference)
            result['torque_off'] = 'TORQUE_RETAINED'
            if (supported_home and (result['status'] == 'RUN_COMPLETE' or result['reason'] == 'Ctrl+C')
                    and result['hold']['status'] in ('CURRENT_POSITION_GOAL_TRANSMITTED', 'HOLD_CONFIRMED')):
                try:
                    sample = io.start_snapshot()
                    moved = live_start.return_home(io, sample, context['home']['state'], 1.0)
                    result['park'] = 'RETURNED_HOME' if moved else 'ALREADY_AT_HOME'
                    result['torque_off'] = io.release_if_supported_home(True)
                except Exception as exc:
                    result.update(park='RELEASE_NOT_COMPLETED', release_error=str(exc))
        except (KeyboardInterrupt, Exception) as exc:
            if io is not None and (io.goal_attempted or getattr(io, 'gripper_overload_fault', None) is not None):
                result['hold'] = base.try_hold(io)
            result['reason'] = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
            if isinstance(exc, KeyboardInterrupt):
                result['status'] = 'STOPPED'
        finally:
            if io is not None:
                result.setdefault('gripper_peak_c', io.gripper_peak)
                result['peak_temperatures_c'] = dict(io.peak_temperatures)
                result.setdefault('camera', io.cameras_stats.summary())
                result.update(gripper_limits_readonly=io.registers, thermal_recheck=io.thermal_recheck,
                              thermal_diagnostics=io.thermal_diagnostics.summary(),
                              thermal_protection=io.thermal_window.summary(),
                              communication=io.communication.summary(),
                              thermal_confirmation=dict(policy=legacy.CONFIG['temperature']['policy'],
                                                        counts=io.thermal_confirmation_counts),
                              startup_temperatures_c=getattr(io, 'initial_temperatures', {}),
                              goal_packet_attempts=getattr(getattr(io, 'permission', None), 'goal_packets', 0))
                try:
                    io.close()
                except Exception as exc:
                    result['cleanup_exception'] = str(exc)
                serial = getattr(getattr(getattr(io, 'bus', None), 'port_handler', None), 'ser', None)
                result.update(port_closed=not bool(getattr(serial, 'is_open', False)),
                              cleanup_errors=io.cleanup_errors)
            if policy is not None:
                try:
                    policy.close()
                except Exception as exc:
                    result['policy_cleanup_exception'] = str(exc)
            result['startup_auto_processed'] = startup
            opening = result.setdefault('gripper_opening_protection', dict(
                open_cap_points=legacy.CONFIG['gripper']['max_open_points'], policy_open_cap_clips=0))
            result['gripper_open_clip_count'] = opening['policy_open_cap_clips']
            result['gripper_overload_recovery'] = getattr(io, 'gripper_overload_recovery', None)
            result.update(output=str(out), configuration_register_writes=0,
                          torque_register_writes=(io.torque_off_writes + io.torque_on_writes) if io else 0)
            if monitor is not None:
                monitor.finish(result['status'], result['reason'], result['steps'], result.get('hold'))
            _write_summary(out, result)
    if not result.get('port_closed') or any(result.get(k) for k in (
            'cleanup_errors', 'cleanup_exception', 'policy_cleanup_exception')):
        return 1
    return {'RUN_COMPLETE': 0, 'STOPPED': 130}.get(result['status'], 1)
