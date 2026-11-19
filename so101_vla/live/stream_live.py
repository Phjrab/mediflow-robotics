"""Buffered SO-101 live runner. Reuses the deployed direct runner's wire guard."""
import argparse
import concurrent.futures
import json
import logging
import math
import os
import statistics
import threading
import time
import unittest
from collections import Counter
from pathlib import Path

import direct_live as base
import live_start
from thermal_read import read_temperatures
from thermal_diagnostics import ThermalDiagnostics
from tracking_compensation import TrackingCompensator
from thermal_window import ThermalWindow
from gripper_safety import OpeningGuard,recover_overload
from gripper_arrival import GripperArrivalGate, gripper_ticks, record_gripper_packet
from grasp_offset import GraspOffset
from legacy_motion_options import GraspAssist, WireRateLimit, load_options
from grasp_recorder import GraspRecorder
from loop_timing import LoopTiming, terminal_lines
from communication_retry import CommunicationPolicy,CommunicationHold,confirmed_hold,retryable_write_error
from vla_monitor import Monitor

from config_runtime import load as load_config,command_limits
CONFIG = load_config()
OVERLAP = CONFIG['overlap_actions']
LOOKAHEAD = CONFIG['lookahead_factor']
MODEL_HZ = CONFIG['model_hz']
DEFAULT_SPEED = CONFIG['speed']
CONTROL_HZ = CONFIG['control_hz']
VIEWER_STOP = '운영자가 관제 화면에서 정지 요청'
ELBOW_INNER_LIMIT = CONFIG['limits']['elbow_flex'][1]
GRIPPER_REGS = {'Max_Torque_Limit': (16, 2), 'Torque_Limit': (48, 2),
                'Protection_Current': (28, 2)}


class GripRelief:
    def __init__(self):
        self.start = None
        self.previous = None
        self.cap = None

    def apply(self, target, position, playback_seconds):
        # This calibrated SO101: synchronized recordings prove increasing = opening.
        # See reports/gripper_direction_20261111; the generic SO100 comment is inapplicable.
        opening = target > position or (self.previous is not None and target > self.previous + 0.25)
        self.previous = target
        if opening:
            self.start = self.cap = None
            return target
        if self.cap is not None:
            self.cap = max(self.cap, position - CONFIG['gripper']['relief_points'])
            return max(target, self.cap)
        if position - target < CONFIG['gripper']['blocked_gap_points']:
            self.start = None
        elif self.start is None or abs(position - self.start[1]) > 1:
            self.start = (playback_seconds, position)
        elif playback_seconds - self.start[0] >= CONFIG['gripper']['blocked_playback_s']:
            self.cap = position - CONFIG['gripper']['relief_points']
            return max(target, self.cap)
        return target


def blend(a, b, fraction):
    return [x + (y - x) * fraction for x, y in zip(a, b, strict=True)]


def merge(queue, incoming, origin, first_unused, *, raw_knots=None):
    offset = max(0, first_unused - origin)
    if offset >= len(incoming):
        return 0
    overlap = 0
    for index in range(origin + offset, min(origin + len(incoming), origin + offset + OVERLAP)):
        if index not in queue:
            break
        overlap += 1
    for k, target in enumerate(incoming[offset:]):
        index = origin + offset + k
        if raw_knots is not None:
            raw_knots[index] = (origin, tuple(target))
        queue[index] = blend(queue[index], target, (k + 1) / overlap) if k < overlap else target
    return overlap


def interpolate(a, b, fraction):
    # Cubic with zero slope at the original action knots. Never overshoots endpoints.
    return blend(a, b, fraction * fraction * (3 - 2 * fraction))


def infer_due(phase, buffer_end, rate, estimated_seconds):
    remaining_seconds = max(0.0, buffer_end - phase) / rate
    return remaining_seconds <= estimated_seconds * LOOKAHEAD


def chunk_alignment(origin, elapsed, rate, first_unused, *, moving):
    # The model timeline pauses when the playback buffer is empty. Wall time
    # can therefore overestimate consumed actions and discard a usable chunk.
    # Keep elapsed/rate in the API for the independent wall-time diagnostic.
    expired = max(0, first_unused - origin) if moving else 0
    return expired, max(first_unused, origin + expired)


def wait_until_cool(read, report, *, pause=time.sleep, sample_interval=None, clock=time.monotonic):
    sample = read()
    if max(sample['temperatures'].values()) < CONFIG['temperature']['wait_at_c']:
        return sample
    next_report = clock()
    while max(sample['temperatures'].values()) >= CONFIG['temperature']['resume_below_c']:
        if sample_interval is None or clock() >= next_report:
            report(sample['temperatures'])
            next_report = clock()+CONFIG['temperature']['poll_s']
        pause(CONFIG['temperature']['poll_s'] if sample_interval is None else sample_interval)
        sample = read()
    return sample


def torque_off_packet(data):
    return (len(data) == 20 and data[:7] == bytes((255, 255, 254, 16, 131, 40, 1))
            and list(data[7:-1:2]) == list(range(1, 7)) and not any(data[8:-1:2])
            and sum(data[2:]) % 256 == 255)


def restrict_to_gripper(permission, expected_goal):
    """Keep the existing read guard and allow only an exact ID-6 position packet."""
    inspect_read = permission.inspect

    def inspect(port, packet):
        data = bytes(packet)
        if len(data) < 5 or data[4] != 0x83:
            return inspect_read(port, packet)
        expected = expected_goal()
        if (not permission.closed and port is permission.port
                and threading.get_ident() == permission.owner and permission.allow_goal
                and permission.phase == 'goal' and permission._allowance == 1 and expected is not None):
            payload = bytes((254, 7, 131, 42, 2, 6)) + expected.to_bytes(2, 'little')
            authorized = b'\xff\xff' + payload + bytes((255 - sum(payload) % 256,))
            if data == authorized:
                permission._allowance = 0
                return 'goal'
        permission.blocked_packets += 1
        raise base.Stop('그리퍼 단독 시험에서 ID 6 위치 목표 외 쓰기 거부')

    permission.inspect = inspect


def frame_brightness(pixels):
    import numpy as np
    # Inspect a spatial sample so checks do not materially slow capture.
    sample = pixels[::8, ::8]
    return {'mean_rgb':float(sample.mean()), 'p99_max_rgb':float(np.quantile(sample.max(axis=2), 0.99))}


class CameraStats(logging.Handler):
    def __init__(self, paths):
        super().__init__(logging.WARNING)
        self.paths = paths
        self.failures = Counter()
        self.last = {}
        self.max_gap = Counter()
        self.overdue = set()
        self.started = {}

    def emit(self, record):
        message = record.getMessage()
        if 'Error reading frame' in message:
            for name, path in self.paths.items():
                if path in message:
                    self.failures[name] += 1

    def frame(self, name):
        now = time.perf_counter()
        if name in self.last:
            self.max_gap[name] = max(self.max_gap[name], now - self.last[name])
        self.last[name] = now

    def heartbeat(self, log):
        now = time.perf_counter()
        for name, started in self.started.items():
            age = now - self.last.get(name, started)
            if age > 1 and name not in self.overdue:
                self.overdue.add(name)
                log({'event': 'camera_gap_over_1s', 'camera': name, 'age_s': age})
            elif age <= 1:
                self.overdue.discard(name)

    def summary(self):
        now = time.perf_counter()
        return {name: {'read_failures': self.failures[name],
                       'max_frame_gap_s': max(self.max_gap[name], now - self.last.get(name, now))}
                for name in self.paths}


def prime_policy(io, policy, pool, log, monitor=None):
    """Validate live images and predict action zero before starting the run budget.

    The control thread continues the existing health/stop checks while the
    inference worker is busy. It does not issue motor goals.
    """
    clock = getattr(io, 'clock', time.monotonic)
    pause = getattr(io, 'pause', time.sleep)
    began = clock()
    frames = None
    while frames is None:
        if monitor is not None and monitor.hold_requested():
            raise base.Stop(VIEWER_STOP)
        sample = io.checked()
        frames = io.fresh_frames()
        if frames is None:
            if clock()-began >= 5:
                raise base.Stop('READY 전 카메라 첫 유효 프레임 5초 초과')
            pause(.02)
    if monitor is not None:
        monitor.capture(frames, sample['state'])
        monitor.status('predicting', sample=sample, force=True)
    requested = clock()
    job = pool.submit(policy, frames, sample['state'], 0, 0)
    while not job.done():
        if monitor is not None and monitor.hold_requested():
            raise base.Stop(VIEWER_STOP)
        io.checked()
        if clock()-requested > 9:
            raise base.Stop('정책 추론 응답 시간 초과')
        pause(.02)
    actions, seconds = job.result()
    chunk = base.chunk(actions)
    # Re-check before READY; a completed worker is not a health certificate.
    io.checked()
    log(dict(event='policy_prime', wall_s=clock()-began, inference_s=seconds,
             frames_verified=True, origin=0, skipped_actions=0))
    return chunk, seconds, clock()-began


def stream(io, policy, log, duration, speed=DEFAULT_SPEED, monitor=None, allow_corrections=False, *, motion_options=None):
    clock = getattr(io, 'clock', time.monotonic)
    pause = getattr(io, 'pause', time.sleep)
    rate = MODEL_HZ * speed
    slots = {}
    raw_knots = {}
    phase = 0.0
    sent_phase = None
    previous_tick = None
    last_send = None
    last_packet_at = last_wire_raw = None
    gap_gripper_packets = 0
    hz = CONTROL_HZ
    estimate = getattr(policy, 'warm_rtc_estimate', 1.5)
    inferences = []
    waits = []
    periods = []
    underrun = None
    count = 0
    max_steps = math.ceil(duration * CONTROL_HZ)
    job = None
    last_origin = -1
    missing_since = None
    reason = None
    hold = {'status': 'NOT_NEEDED'}
    last_model = last_wire = None
    last_knot = None
    correction = None
    observed = [[] for _ in base.JOINTS]
    commands = [[] for _ in base.JOINTS]
    relief = GripRelief()
    opening_guard = OpeningGuard()
    close_gate = GripperArrivalGate(log)
    assist = GraspAssist(motion_options.close_median) if motion_options and motion_options.assist_grasp else None
    limiter = WireRateLimit(io, motion_options.rates) if motion_options and motion_options.rates else None
    option_meta = {'motion_options': motion_options.metadata()} if motion_options else {}
    assist_row = None
    pending_assist_until = None
    grip_bounds = getattr(io, 'raw_bounds', getattr(io, 'arm_bounds', {})).get('gripper')
    grasp_offset = GraspOffset()
    compensation_bounds = getattr(io, 'bounds', None)
    if compensation_bounds is None and hasattr(io, 'normalize') and hasattr(io, 'arm_bounds'):
        low = io.normalize({j: io.arm_bounds[j][0] for j in base.JOINTS})
        high = io.normalize({j: io.arm_bounds[j][1] for j in base.JOINTS})
        compensation_bounds = [tuple(sorted(pair)) for pair in zip(low, high)]
    compensator = TrackingCompensator(base.JOINTS, CONFIG.get('tracking_compensation', {}),
                                     bounds=compensation_bounds)
    pool_factory = getattr(io, 'pool_factory', concurrent.futures.ThreadPoolExecutor)
    pool = pool_factory(max_workers=1)
    # File/hash/FK setup does not consume the operator's motion-loop budget.
    started = clock()
    deadline = started + duration
    next_tick = started
    next_preview = started
    ready = None
    recorder = None
    recording = None
    timing = None
    timing_summary = None
    try:
        incoming, seconds, prime_s = prime_policy(io, policy, pool, log, monitor)
        slots.update(enumerate(incoming))
        raw_knots.update((index, (0, tuple(row))) for index, row in enumerate(incoming))
        inferences.append(seconds)
        estimate = max(seconds, estimate*.85)
        last_origin = 0
        # Model loading, warmup, first images and first inference are all outside
        # the duration. Only the ready policy loop consumes this deadline.
        if getattr(io, 'out', None) is not None:
            recorder = GraspRecorder(io.out, base.JOINTS, clock=clock)
        started = clock()
        deadline = started + duration
        next_tick = next_preview = started
        ready = dict(event='ready', unix=time.time(), monotonic_s=started,
                     duration_s=duration, speed=speed, first_inference_s=seconds,
                     first_observation_and_inference_s=prime_s, **option_meta)
        timing = LoopTiming(started, speed, log, clock=clock)
        print('READY ' + json.dumps(ready, ensure_ascii=False), flush=True)
        log(ready)
        log(dict(event='inference', origin=0, seconds=seconds, overlap=0,
                 skipped_actions=0, expired_actions=0, discarded=False,
                 wall_time_expired_actions=0, first_unused_at_delivery=0,
                 incoming_actions=incoming, time_s=0., primed_before_ready=True))
        while count < max_steps:
            pause(max(0, min(deadline, next_tick) - clock()))
            tick_started = clock()
            if tick_started >= deadline:
                break
            tick = timing.begin(tick_started, sent_phase)
            tick_failed = True
            try:
                if monitor is not None and monitor.hold_requested():
                    raise base.Stop(VIEWER_STOP)
                if allow_corrections:
                    correction = monitor.take_correction()
                    if correction is not None:
                        tick_failed = False
                        break
                next_tick = tick_started + 1 / hz
                sample = tick.call('serial_read', io.checked)
                if recorder is not None and recorder.due(clock()):
                    captured_at = clock()
                    tick.call('recorder_enqueue', recorder.capture,
                                 tick.call('camera', io.camera_frames) if hasattr(io,'camera_frames') else None,
                                     sample, time_s=captured_at-started, monotonic=captured_at)
                if monitor is not None and tick_started >= next_preview:
                    view = tick.call('camera', io.camera_frames) if hasattr(io,'camera_frames') else None
                    if view is not None:tick.call('preview_enqueue', monitor.capture, view, sample['state'])
                    next_preview=tick_started+.2
                for track, value in zip(observed, sample['state']):
                    track.append(value)
                buffer_end = max(slots, default=-1)
                if previous_tick is not None and sent_phase is not None and buffer_end > phase:
                    phase = min(phase + (tick_started - previous_tick) * rate, buffer_end)
                previous_tick = tick_started
                first_unused = math.floor(phase) + 1 if sent_phase is not None else 0
                for index in tuple(slots):
                    if index < math.floor(phase):
                        del slots[index]
                        del raw_knots[index]
                if job is not None and job.done():
                    actions, seconds = tick.call('inference_blocking', job.result)
                    tick.inference_ms = seconds*1000
                    timing.inference(seconds)
                    inferences.append(seconds)
                    estimate = max(seconds, estimate * 0.85)
                    incoming = base.chunk(actions)
                    expired, usable = chunk_alignment(origin, seconds, request_rate, first_unused, moving=request_moving)
                    skip = usable - origin
                    # An expired chunk cannot bridge the current playback cursor. Refresh
                    # from the held pose if the existing buffer runs out.
                    discarded = skip >= len(incoming) or usable > buffer_end + 1
                    overlap = 0 if discarded else merge(slots, incoming, origin, usable, raw_knots=raw_knots)
                    blend_delta = [max((abs(slots[k][i] - incoming[k-origin][i])
                                        for k in range(usable, usable+overlap)), default=0) for i in range(6)]
                    log({'event': 'inference', 'origin': origin, 'seconds': seconds,
                         'time_s': clock()-started, 'incoming_actions': incoming,
                         'overlap': overlap, 'overlap_playback_s': overlap / MODEL_HZ,
                         'overlap_wall_s': overlap / rate, 'playback_hz': rate,
                         'elapsed_speed_actions': seconds * rate, 'expired_actions': expired,
                         'wall_time_expired_actions': math.floor(seconds * request_rate) if request_moving else 0,
                         'alignment_basis': 'actually_consumed_playback_actions',
                         'first_unused_at_delivery': first_unused,
                         'skipped_actions': skip, 'discarded': discarded,
                         'blend_max_delta': blend_delta})
                    if seconds * rate * 1.1 > 50:
                        rate = min(rate, 50 / (seconds * 1.1))
                        log({'event':'playback_rate_reduced','requested_speed':speed,'actual_playback_hz':rate})
                    job = None
                if job is not None and tick_started - requested > 9:
                    raise base.Stop('정책 추론 응답 시간 초과')
                buffer_end = max(slots, default=-1)
                remaining = max(0, buffer_end - first_unused + 1)
                if (job is None and infer_due(phase, buffer_end, rate, estimate)
                        and (remaining == 0 or first_unused > last_origin)):
                    frames = tick.call('camera', io.fresh_frames)
                    if frames is not None:
                        missing_since = None
                        requested = clock()
                        origin = first_unused
                        last_origin = origin
                        request_moving = sent_phase is not None and buffer_end > phase
                        request_rate = rate
                        delay = min(remaining, math.ceil(rate * estimate)) if request_moving else 0
                        log(dict(event='inference_requested', time_s=requested-started,
                                 origin=origin, buffer_end=buffer_end, phase=phase,
                                 remaining_actions=remaining, remaining_wall_s=remaining/rate,
                                 estimated_inference_s=estimate, playback_hz=rate))
                        job = tick.call('inference_blocking', pool.submit, policy, frames, sample['state'], origin, delay)
                        if monitor is not None:
                            tick.call('preview_enqueue', monitor.capture, frames, sample['state'])
                    elif missing_since is None:
                        missing_since = tick_started
                    elif tick_started - missing_since > 8:
                        raise base.Stop('사용 가능한 카메라 영상 없음: 프레임 지연 또는 너무 어두운 영상')
                lower, upper = math.floor(phase), math.ceil(phase)
                if lower not in slots or upper not in slots or (phase == sent_phase and phase >= buffer_end):
                    assist_due = pending_assist_until is not None and clock() >= pending_assist_until
                    if (close_gate.started is not None or assist_due) and last_knot is not None:
                        # Complete an already requested close using fresh feedback.
                        # No new policy phase or arm goal is consumed during a gap.
                        saved_index, saved_origin, knot, requested_ticks = last_knot
                        gate_was_waiting = close_gate.started is not None
                        gated = close_gate.apply(last_model, sample['state'], clock()-started,
                                                 policy_knot=knot, knot_index=saved_index, knot_origin=saved_origin,
                                                 requested_ticks=requested_ticks, present_ticks=sample.get('present_raw', {}).get('gripper'))
                        if close_gate.started is None and (pending_assist_until is None or assist_due):
                            pending_assist_until = None
                            relieved = relief.apply(gated, sample['state'][-1], sent_phase / MODEL_HZ)
                            protected = opening_guard.apply(relieved, sample['state'][-1],
                                                            sample.get('loads', {}).get('gripper', 0), clock())
                            grip_plan = io.plan([*last_wire[:-1], protected])
                            rate_row = None
                            if limiter:
                                grip_plan['wire_raw'] = dict(last_wire_raw, gripper=grip_plan['wire_raw']['gripper'])
                                rate_row = limiter.apply(grip_plan, sample, last_wire_raw, min(1/hz, clock()-last_packet_at),
                                                         protect_gripper=relieved!=gated or opening_guard.fixed is not None)
                            wire = dict(last_wire_raw, gripper=grip_plan['wire_raw']['gripper'])
                            if wire != last_wire_raw:
                                tick.call('cadence_wait', pause, max(0., min(deadline, last_packet_at+1/hz)-clock()))
                                if clock() >= deadline:
                                    tick_failed = False
                                    break
                                if monitor is not None and monitor.hold_requested():
                                    raise base.Stop(VIEWER_STOP)
                                if allow_corrections:
                                    correction = monitor.take_correction()
                                    if correction is not None:
                                        tick_failed = False
                                        break
                                tick.call('serial_write', io.send, wire)
                                last_packet_at = clock()
                                last_wire_raw = wire
                                last_wire = [*last_wire[:-1], grip_plan['wire_normalized'][-1]]
                                gap_gripper_packets += 1
                                row = dict(event='gripper_safety_goal',
                                           reason='gripper_close_wait_release' if gate_was_waiting else 'grasp_assist_release',
                                           decision=close_gate.events[-1]['decision'] if gate_was_waiting else 'assist_hold_complete',
                                           mode='legacy', step=count,
                                           model_phase=sent_phase, playback_s=sent_phase/MODEL_HZ,
                                           policy_index_advanced=False, time_s=last_packet_at-started,
                                           model=last_model, feedback=sample, raw_upper=knot,
                                           raw_upper_index=saved_index, raw_upper_origin=saved_origin,
                                           gripper_gate_points=gated, protected_points=protected,
                                           grip_relief=relieved!=gated, wire_raw=wire, wire_normalized=last_wire,
                                           **option_meta)
                                if assist_row is not None:
                                    row['grasp_assist'] = dict(assist_row, holding=False)
                                if rate_row is not None:
                                    row['data_velocity_limit'] = rate_row
                                record_gripper_packet(tick, log, recorder, row, last_packet_at)
                    if last_send is not None and underrun is None:
                        underrun = tick_started
                    if monitor is not None:
                        tick.call('preview_enqueue', monitor.status, 'predicting' if job is not None else 'observing', step=count,
                                       sample=sample, model=last_model, sent=last_wire,
                                       inference_seconds=inferences[-1] if inferences else None)
                    tick.action_starved = True
                    tick_failed = False
                    continue
                model = interpolate(slots[lower], slots[upper], phase - lower)
                target = model.copy()
                knot_origin, raw_upper = raw_knots[upper]
                future=[slots[k] for k in range(upper+1,min(buffer_end+1,upper+math.ceil(rate)+1)) if k in slots] if grasp_offset.enabled else []
                target,lateral=grasp_offset.apply(target,sample['state'],tick_started,future,rate)
                if assist:
                    assist_row = assist.apply(model, raw_upper, sample, tick_started)
                    target[:5] = [value+delta for value, delta in zip(target, assist_row['offset_deg'])]
                policy_plan = io.plan(target)
                requested_ticks = gripper_ticks(policy_plan)
                gated_points = close_gate.apply(model, sample['state'], clock()-started, policy_knot=raw_upper,
                                                knot_index=upper, knot_origin=knot_origin, requested_ticks=requested_ticks,
                                                present_ticks=sample.get('present_raw', {}).get('gripper'))
                assist_hold = assist_row and assist_row['holding'] and close_gate.started is None
                grip_demand = assist.held_points if assist_hold else gated_points
                target[-1] = relief.apply(grip_demand, sample['state'][-1], phase / MODEL_HZ)
                closing_relief=target[-1]!=grip_demand
                target[-1] = opening_guard.apply(target[-1],sample['state'][-1],sample.get('loads',{}).get('gripper',0),tick_started)
                reference_plan = policy_plan if target[-1] == model[-1] else io.plan(target)
                close_gate.preserve_held_tick(reference_plan, target[-1], grip_bounds)
                compensated, compensation = compensator.apply(reference_plan['wire_normalized'],
                                                              sample['state'], tick_started)
                # Preserve the original SDK encoding on axes without a correction;
                # normalizing and encoding the same integer again can lose one tick.
                compensated = [original if offset == 0 else corrected for original, offset, corrected in
                               zip(target, compensation['offset_deg'], compensated)]
                plan = io.plan(compensated)
                close_gate.preserve_held_tick(plan, compensated[-1], grip_bounds)
                if (assist_hold and compensated[-1] == assist.held_points and
                        grip_bounds[0] <= assist.held_tick <= grip_bounds[1] and
                        plan.get('bounded', compensated)[-1] == compensated[-1]):
                    plan['wire_raw']['gripper'] = assist.held_tick
                    plan['wire_normalized'][-1] = assist.held_points
                plan['clipped_joints'] = sorted(set(plan.get('clipped_joints', ())) |
                                                set(reference_plan.get('clipped_joints', ())))
                compensation['uncompensated_raw'] = reference_plan['wire_raw']
                compensation['wire_offset_deg'] = [a-b for a, b in
                                                   zip(plan['wire_normalized'], reference_plan['wire_normalized'])]
                prepared = clock()
                if last_send is not None:
                    tick.call('cadence_wait', pause, max(0, min(deadline, last_packet_at + 1 / hz) - prepared))
                send_started = clock()
                if send_started >= deadline:
                    tick_failed = False
                    break
                if monitor is not None and monitor.hold_requested():
                    raise base.Stop(VIEWER_STOP)
                if allow_corrections:
                    correction = monitor.take_correction()
                    if correction is not None:
                        tick_failed = False
                        break
                rate_row = None
                if limiter:
                    dt = 0. if last_packet_at is None else min(1/hz, send_started-last_packet_at)
                    rate_row = limiter.apply(plan, sample, last_wire_raw, dt,
                                             protect_gripper=bool(assist_hold or close_gate.started is not None or
                                                                  closing_relief or opening_guard.fixed is not None))
                # Repeat a quantized target when the interpolated motion is smaller
                # than one motor tick. Model speed must not lower the packet cadence.
                tick.call('serial_write', io.send, plan['wire_raw'])
                sent = clock()
                sent_phase = phase
                loop_work = prepared - tick_started + sent - send_started
                gap = None if last_send is None else sent - last_send
                if gap is not None and underrun is None:
                    periods.append(gap)
                if underrun is not None:
                    waits.append(max(0, sent - underrun))
                    underrun = None
                for track, value in zip(commands, plan['wire_normalized']):
                    track.append(value)
                goal_record = {'event': 'goal_transmitted', 'step': count + 1, 'time_s': sent-started,
                     'gap_s': gap, 'hz_target': hz, 'loop_work_s': loop_work,
                     'model_phase': phase, 'playback_s': phase / MODEL_HZ,
                     'feedback': sample, 'model': model,
                     'raw_upper': raw_upper, 'raw_upper_index': upper, 'raw_upper_origin': knot_origin,
                     'policy_gripper_ticks': requested_ticks,
                     'gripper_gate_points': gated_points, 'gripper_waiting': close_gate.started is not None,
                     'grip_relief': closing_relief,
                     'gripper_opening':dict(cap_clipped=model[-1]>opening_guard.cap,limited_points=target[-1],load_hold_points=opening_guard.fixed),
                     'lateral_correction':lateral,
                     'tracking_compensation': compensation, **option_meta, **plan}
                if assist_row is not None:
                    goal_record['grasp_assist'] = assist_row
                if rate_row is not None:
                    goal_record['data_velocity_limit'] = rate_row
                log(goal_record)
                if recorder is not None:
                    event = tick.call('recorder_enqueue', recorder.goal, plan['wire_normalized'][-1], goal_record,
                                          time_s=sent-started, monotonic=sent)
                    if event is not None:
                        log(dict(event='grasp_event', **event))
                count += 1
                last_send = sent
                last_packet_at, last_wire_raw = sent, plan['wire_raw']
                last_model, last_wire = model, plan['wire_normalized']
                last_knot = (upper, knot_origin, raw_upper, requested_ticks)
                pending_assist_until = assist.release_at if assist_row and assist_row['holding'] else None
                if monitor is not None:
                    tick.call('preview_enqueue', monitor.status, 'moving', step=count, sample=sample, model=last_model, sent=last_wire,
                                   inference_seconds=inferences[-1] if inferences else None)
                if hz == CONTROL_HZ and loop_work > 0.016:
                    hz = 50
                    next_tick = tick_started + 1 / hz
                    log({'event': 'cadence_fallback', 'loop_work_s': loop_work, 'hz_target': hz})
                tick_failed = False
            finally:
                tick.inference_pending = job is not None
                tick.camera_missing = missing_since is not None
                tick.finish(phase=sent_phase, selected_phase=phase, failed=tick_failed)
    except (KeyboardInterrupt, Exception) as exc:
        reason = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
    finally:
        timing_ended = clock()
        close_gate.finish(timing_ended-started)
        if io.goal_attempted:
            hold = base.try_hold(io)
            if hasattr(io, 'confirm_thermal_stop'):
                io.confirm_thermal_stop()
        pool.shutdown(wait=False, cancel_futures=True)
        loop_elapsed = clock()-started
        if recorder is not None:
            recording = recorder.close(reason=reason or 'run_end')
            print('집기 이벤트 ' + str(recording['event_count']) + '회 · 시각(READY 이후 초) ' +
                  ', '.join(f"{e['time_s']:.3f}" for e in recording['events']), flush=True)
        if timing is not None:
            timing_summary = timing.summary(timing_ended)
            for line in terminal_lines(timing_summary): print(line, flush=True)
    if underrun is not None:
        waits.append(max(0, min(deadline, tick_started) - underrun))
    tracking = {}
    for i, name in enumerate(base.JOINTS):
        command_span = max(commands[i]) - min(commands[i]) if commands[i] else 0
        observed_span = max(observed[i]) - min(observed[i]) if observed[i] else 0
        tracking[name] = {'sent_span': command_span, 'observed_span': observed_span,
                          'span_ratio': observed_span/command_span if command_span else None}
    if not count and reason is None:
        reason = '실행 시간 안에 정책 목표가 전송되지 않음'
    status = 'STOPPED' if reason == 'Ctrl+C' else 'HOLD' if reason else 'RUN_COMPLETE'
    return {'status': status, 'reason': reason, 'hold': hold,
            'operator_request': correction,
            'ready': ready,
            'grasp_recording': recording,
            'loop_timing': timing_summary,
            'steps': count, 'step_limit': max_steps, 'elapsed_s': loop_elapsed,
            'task_success': 'UNVERIFIED',
            'speed': speed, 'playback_hz': rate, 'playback_s': phase / MODEL_HZ,
            'gripper_peak_c': io.gripper_peak, 'peak_temperatures_c': dict(io.peak_temperatures),
            'camera': io.cameras_stats.summary(),
            'inference_s': {'mean': statistics.mean(inferences) if inferences else None, 'samples': inferences},
            'chunk_wait_s': {'max': max(waits, default=0), 'sum': sum(waits)},
            'transmission_hz': 1/statistics.mean(periods) if periods else None, 'final_rate_target': hz,
            'tracking': tracking, 'tracking_compensation': compensator.summary(),
            'gripper_close_wait': close_gate.summary(),
            'gripper_close_gap_packets': gap_gripper_packets,
            'gripper_opening_protection': opening_guard.summary(),
            'lateral_correction':grasp_offset.summary(), **option_meta,
            **({'grasp_assist': dict(windows=assist.windows, applied_steps=assist.steps)} if assist else {})}


def device_class():
    from bounded_wait import wait
    from individual_reply_guard import StrictIndividualReplies
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera

    class StreamingIO(base.device_class()):
        clock = staticmethod(time.monotonic)
        pause = staticmethod(time.sleep)
        home_path_verified = False

        def __init__(self, context, log, out, *, gripper_only=False):
            super().__init__(context, None)
            self.gripper_only = gripper_only
            self.expected_gripper = None
            self.log = log
            self.gripper_peak = None
            self.peak_temperatures = {}
            self.temperature_fault = None
            self.thermal_recheck = {}
            self.thermal_disagreement = {}
            self.thermal_confirmation_counts = {j: dict(spike=0, confirmed=0) for j in base.JOINTS}
            self.thermal_window = ThermalWindow(base.JOINTS, CONFIG['temperature'])
            self.temperature_last_requests = {}
            self.temperature_fault_decisions = {}
            self.thermal_diagnostics = ThermalDiagnostics(base.JOINTS)
            self.thermal_sequence = 0
            self.communication=CommunicationPolicy(base.JOINTS,CONFIG['communication'],self._log_wire_event,
                                                   clock=self.clock,pause=self.pause)
            self.communication_fault=False
            self.mode_cache=None
            self.mode_cached_at=None
            self.torque_off_writes = 0
            self.torque_on_writes = 0
            self.startup_pending = True
            self.motion_ready = False
            self.arm_bounds=command_limits(CONFIG)
            self.gripper_overload_fault=None
            self.gripper_overload_recovery=None
            self.recovering_gripper=False
            self.out = out
            self.saved_first_frames = False
            self.dark_cameras = set()
            self.registers = {}
            self.cameras_stats = CameraStats(context['contract']['cameras'])
            self.camera_logger = logging.getLogger('lerobot.cameras.opencv.camera_opencv')
            self.camera_logger.addHandler(self.cameras_stats)

        def _log_wire_event(self, row):
            if row.get('event') == 'wire_rx':
                self.thermal_diagnostics.record_wire_reply(row, sequence=self.communication.rx_count)
            self.log(row)

        def check_start_mode(self):
            if self.gripper_only:
                return super().check_start_mode()
            self.raw_bounds = command_limits(CONFIG)
            self.arm_bounds = self.raw_bounds.copy()
            self.permission.READS = {**self.permission.READS, 67: 2}
            self.read_modes()

        def configure_transport(self):
            self.permission.READS={**self.permission.READS,33:8,67:2}
            self.read_ids={j:mid for mid in CONFIG['communication']['read_order'] for j,value in self.ids.items() if value==mid}
            if len(self.read_ids)!=6 or set(self.read_ids)!=set(base.JOINTS):raise base.Stop('읽기 ID 순서 불일치')
            self.communication.attach(self.bus.port_handler)

        def read_modes(self):
            if self.mode_cache is None or self.clock()-self.mode_cached_at>.05:
                self.read_block(33,8)
            block=self.mode_cache
            modes = {j: row[0] for j, row in block.items()}
            torque = {j: row[7] for j, row in block.items()}
            if any(modes.values()) or any(value not in (0, 1) for value in torque.values()):
                raise base.Stop('위치 모드 또는 토크 응답 오류: ' + repr((modes, torque)))
            return modes, torque

        def start_snapshot(self):
            sample = self.checked()
            sample['modes'], sample['torque'] = self.read_modes()
            sample['active_targets'] = {j: int.from_bytes(row, 'little') for j, row in self.read_block(67, 2).items()}
            return sample

        def write_start(self, register, goals):
            return self.communication.perform([self.ids[j] for j in goals],f'write_start:{register}',
                lambda:self._write_start_once(register,goals),port=self.bus.port_handler,response=False)

        def _write_start_once(self, register, goals):
            if self.gripper_only or self.wire_fault or not goals or not goals.keys() <= self.ids.keys():
                raise base.Stop('시작 패킷 범위 위반')
            if register == 'Goal_Position' and all(type(value) is int and self.arm_bounds[j][0] <= value <= self.arm_bounds[j][1]
                                                  for j, value in goals.items()):
                address, width = 42, 2
            elif register == 'Torque_Enable' and all(type(value) is int and value == 1 for value in goals.values()):
                address, width = 40, 1
            elif (register=='Torque_Enable' and getattr(self,'recovering_gripper',False)
                    and goals=={'gripper':0}):
                address,width=40,1
            else:
                raise base.Stop('허용되지 않은 시작 레지스터 또는 값')
            expected = live_start.exact_sync_packet(address, width, {self.ids[j]: value for j, value in goals.items()})
            permission = self.permission
            original = permission.inspect
            sent = False

            def inspect(port, packet):
                nonlocal sent
                if (not sent and not permission.closed and permission.phase == 'read' and permission.allow_goal
                        and port is permission.port and threading.get_ident() == permission.owner and bytes(packet) == expected):
                    sent = True
                    if address == 40:
                        if set(goals.values())=={0}:
                            self.torque_off_writes+=1
                            return 'torque_off'
                        self.torque_on_writes += 1
                        return 'torque_on'
                    return 'goal'
                return original(port, packet)

            permission.inspect = inspect
            try:
                self.bus.sync_write(register, goals, normalize=False, num_retry=0)
                if not sent:
                    raise base.Stop('검증된 시작 패킷을 보내지 못했습니다')
                self.log({'event': 'gripper_recovery_write' if getattr(self,'recovering_gripper',False) else 'startup_write', 'register': register, 'values': goals})
            except Exception as error:
                retry=retryable_write_error(error,dict(register=register,goals=goals))
                if retry is not None:raise retry from error
                self.wire_fault=True
                raise
            finally:
                permission.inspect = original

        def read_block(self, address, width):
            requested=(address,width)
            source=requested
            try:data = super().read_block(*source)
            except base.ServoStatusStop as error:
                packet=error.packet
                eligible=(error.mid==6 and error.status==32 and error.replies is not None
                          and set(error.replies)==set(base.JOINTS) and source in ((56,15),(33,8),(67,2))
                          and len(packet)==width+6 and packet[:2]==b'\xff\xff' and packet[2:5]==bytes((6,width+2,32))
                          and sum(packet[2:])%256==255 and all(len(row)==width for row in error.replies.values()))
                if not eligible:
                    self.wire_fault=True
                    raise
                self.gripper_overload_fault=error
                self.log(dict(event='gripper_overload_response',address=address,width=width,
                              response_id=6,status=32,raw_hex=error.packet.hex(),policy_continues=False))
                if not getattr(self,'recovering_gripper',False):raise
                # This acknowledges a validated transport reply, not a healthy
                # servo. The overload latch still forces HOLD and never resume.
                self.communication.good(6,f'read:{address}:{width}')
                data=error.replies
            except BaseException as error:
                if isinstance(error,CommunicationHold):self.communication_fault=True
                else:self.wire_fault=True
                self.log({'event': 'thermal_read_error', 'source': 'feedback_block',
                          'address': address, 'width': width,
                          **self.thermal_diagnostics.record_error(error)})
                raise
            if source==(33,8):
                self.mode_cache=data
                self.mode_cached_at=self.clock()
            if address == 56:
                self.feedback_bytes = data
            return data

        def _send_once(self,raw):
            if self.gripper_only:return self._send_gripper_once(raw)
            if set(raw)!=set(base.JOINTS) or any(type(raw[j]) is not int or not lo<=raw[j]<=hi for j,(lo,hi) in self.raw_bounds.items()):
                raise base.Stop('SDK 전송 전 정수 목표 범위 오류')
            if raw['gripper']>command_limits(CONFIG)['gripper'][1]:raise base.Stop('그리퍼 열림 상한 초과 전송 금지')
            self.expected={self.ids[j]:raw[j] for j in base.JOINTS};self.goal_attempted=True
            try:
                with self.permission.goal_once():self.bus.sync_write('Goal_Position',raw,normalize=False,num_retry=0)
            except Exception as error:
                retry=retryable_write_error(error,dict(register='Goal_Position',goals=raw))
                if retry is not None:raise retry from error
                self.wire_fault=True;raise
            finally:self.expected=None

        def _send_policy(self,raw):
            if self.wire_fault:raise base.Stop('비재시도 송신 오류; 추가 목표 금지')
            if self.motion_ready:self.communication.activate()
            return self.communication.perform(list(self.ids.values()),'write:42:2',lambda:self._send_once(raw),
                port=self.bus.port_handler,response=False)

        def _hold_policy(self):
            if getattr(self,'gripper_overload_fault',None) is not None and not self.wire_fault:
                if self.gripper_overload_recovery is None:
                    self.gripper_overload_recovery=recover_overload(self)
                    self.log(dict(event='gripper_overload_hold',**self.gripper_overload_recovery))
                return self.gripper_overload_recovery
            if self.wire_fault:raise base.Stop('비재시도 오류로 위치 유지 쓰기 금지')
            return confirmed_hold(self,self.communication,('gripper',) if self.gripper_only else base.JOINTS)

        def write_recovery_gripper(self,register,value):
            if not getattr(self,'recovering_gripper',False) or register not in ('Goal_Position','Torque_Enable'):
                raise base.Stop('그리퍼 복구 쓰기 범위 위반')
            self.write_start(register,{'gripper':value})

        def recovery_snapshot(self):
            if not self.recovering_gripper or self.wire_fault or self.temperature_fault:
                raise base.Stop('그리퍼 복구 중 통신/온도 상태 불가')
            data=self.read_block(56,15)
            status={j:row[9] for j,row in data.items()}
            if any(v not in ((0,32) if j=='gripper' else (0,)) for j,v in status.items()):
                raise base.Stop('그리퍼 복구 대상 외 상태 오류: '+repr(status))
            if any(row[7]>=CONFIG['temperature']['stop_c'] for row in data.values()):
                raise base.Stop('그리퍼 복구 읽기의 고온 응답; 토크 재활성화 금지')
            raw={j:int.from_bytes(row[:2],'little') for j,row in data.items()}
            live_start.validate_position({'present_raw':raw},command_limits(CONFIG))
            modes=self.read_block(33,8);goals=self.read_block(67,2)
            return dict(present_raw=raw,statuses=status,modes={j:row[0] for j,row in modes.items()},
                        torque={j:row[7] for j,row in modes.items()},
                        active_targets={j:int.from_bytes(row,'little') for j,row in goals.items()})

        def latch_gripper_status(self,sample):
            status=sample['statuses']
            if status.get('gripper')==32 and all(status.get(j)==0 for j in base.JOINTS if j!='gripper'):
                self.gripper_overload_fault=dict(source='validated_feedback_status',status=32)
                self.log(dict(event='gripper_overload_status',status=32,policy_continues=False))

        def feedback(self):
            sample = super().feedback()
            self.latch_gripper_status(sample)
            bulk_observed_s = time.monotonic()
            self.read_block(33,8)
            sample['temperatures_bulk'] = dict(sample['temperatures'])
            sample['voltage_raw'] = {j: self.feedback_bytes[j][6] for j in base.JOINTS}
            sample['voltage_v'] = {j: value / 10 for j, value in sample['voltage_raw'].items()}
            sample['current_raw'] = {}
            for name, mid in self.ids.items():
                value = int.from_bytes(self.feedback_bytes[name][13:15], 'little')
                sample['current_raw'][name] = self.bus._decode_sign('Present_Current', {mid: value})[mid]
            self.communication.update_pose(sample)
            traces = []
            try:
                sample['temperatures'], disagreement = read_temperatures(
                    self.bus, self.ids, sample['temperatures_bulk'], StrictIndividualReplies, traces=traces,
                    last_requests=self.temperature_last_requests,
                    minimum_interval_s=CONFIG['temperature']['sample_interval_s'], clock=self.clock, pause=self.pause,
                    retry=self.communication)
            except Exception as exc:
                if isinstance(exc,CommunicationHold):self.communication_fault=True
                else:self.wire_fault=True
                self.log({'event': 'thermal_read_error', 'source': 'individual_temperature',
                          'reply_trace': traces, 'temperature_bulk_c': sample['temperatures_bulk'],
                          'voltage_v': sample['voltage_v'],
                          'feedback_bytes_hex': {j: data.hex() for j, data in self.feedback_bytes.items()},
                          **self.thermal_diagnostics.record_error(exc)})
                if isinstance(exc,CommunicationHold):raise
                raise base.Stop('개별 온도 응답 검증 실패; 추가 쓰기 금지: ' + str(exc)) from exc
            # Individual requests own the protection window; bulk values and the
            # old display-only diagnostic filter never override its decision.
            source_values = {j: [sample['temperatures_bulk'][j]] +
                             [row['value_c'] for row in traces if row.get('joint') == j and 'value_c' in row]
                             for j in base.JOINTS}
            sample['temperatures_raw'] = {j: max(values) for j, values in source_values.items()}
            sample_times = {row['joint']: row['observed_monotonic_s'] for row in traces}
            sample['temperature_protection'] = {j: self.thermal_window.observe_axis(
                j, sample_times[j], sample['temperatures'][j]) for j in base.JOINTS}
            for joint, row in sample['temperature_protection'].items():
                self.thermal_confirmation_counts[joint]['spike'] = row['spike_count']
                if row['hold'] and joint not in self.temperature_fault_decisions:
                    self.thermal_confirmation_counts[joint]['confirmed'] += 1
                if row['ended_spike'] or row['hold'] or row['warning_started']:
                    self.log({'event': 'thermal_window_event', 'joint': joint, **row})
                if row['warning_started']:
                    print(f'온도 경고: {joint} 2초 중앙값 {row["median_c"]:.1f}°C',flush=True)
            sample['thermal_diagnostics'] = self.thermal_diagnostics.observe(
                time.monotonic(), sample['temperatures_raw'], sample['voltage_v'], sample['statuses'], source_values)
            suspect = [j for j in base.JOINTS if sample['temperatures_raw'][j] >= CONFIG['temperature']['stop_c']
                       or sample['thermal_diagnostics'][j]['suspected_spike']]
            if suspect:
                sample['modes'], sample['torque'] = self.read_modes()
                sample['torque_observed_monotonic_s'] = self.mode_cached_at
                for joint in suspect:
                    self.log({'event': 'thermal_spike_context', 'joint': joint,
                        'temperature_individual_c': sample['temperatures'][joint],
                        'temperature_bulk_c': sample['temperatures_bulk'][joint],
                        'voltage_v': sample['voltage_v'][joint], 'load_raw': sample['loads'][joint],
                        'current_raw': sample['current_raw'][joint], 'torque': sample['torque'][joint],
                        'status': sample['statuses'][joint], 'decision': sample['temperature_protection'][joint],
                        'bulk_observed_monotonic_s': bulk_observed_s,
                        'torque_observed_monotonic_s': sample['torque_observed_monotonic_s'],
                        'feedback_bytes_hex': self.feedback_bytes[joint].hex(),
                        'reply_trace': [r for r in traces if r.get('joint') == joint]})
            self.thermal_sequence += 1
            self.log({'event': 'thermal_cycle', 'sequence': self.thermal_sequence,
                      'monotonic_s': time.monotonic(), 'temperature_raw_c': dict(sample['temperatures_raw']),
                      'temperature_individual_c': dict(sample['temperatures']),
                      'temperature_protection': sample['temperature_protection'],
                      'temperature_bulk_c': sample['temperatures_bulk'], 'voltage_v': sample['voltage_v'],
                      'bulk_observed_monotonic_s': bulk_observed_s,
                      'voltage_association': 'same_packet_with_bulk_temperature_only',
                      'statuses': dict(sample['statuses']), 'diagnostics': sample['thermal_diagnostics'],
                      'reply_trace': traces,
                      'feedback_bytes_hex': {j: data.hex() for j, data in self.feedback_bytes.items()},
                      'error_counts': dict(self.thermal_diagnostics.errors), 'filter_controls_motion': False})
            if disagreement != self.thermal_disagreement:
                if disagreement:
                    self.log({'event': 'thermal_source_disagreement', 'joints': disagreement})
                self.thermal_disagreement = disagreement
            if not hasattr(self,'initial_temperatures'):self.initial_temperatures=dict(sample['temperatures_raw'])
            temperature = sample['temperatures_raw']['gripper']
            for joint, value in sample['temperatures_raw'].items():
                self.peak_temperatures[joint] = max(value, self.peak_temperatures.get(joint, value))
            if self.gripper_peak is None:
                self.log({'event': 'startup_temperatures', 'temperature_c': sample['temperatures']})
                print('시작 온도(°C) ' + json.dumps(sample['temperatures']), flush=True)
                if temperature >= 45:
                    print(f'그리퍼 시작 온도 경고 {temperature}°C', flush=True)
            self.gripper_peak = max(self.gripper_peak or temperature, temperature)
            hot = {j: sample['temperatures'][j] for j, row in sample['temperature_protection'].items() if row['hold']}
            if hot:
                self.temperature_fault = {**(self.temperature_fault or {}), **hot}
                self.temperature_fault_decisions.update({j:sample['temperature_protection'][j] for j in hot})
            # Collection must remain available to the existing current-position HOLD.
            return sample

        def checked(self):
            if self.startup_pending:
                def startup_sample():
                    feedback = self.feedback()
                    if (any(feedback['statuses'].values()) or any(
                            row['hold'] for row in feedback['temperature_protection'].values())):
                        base.feedback_gate(feedback)
                    return feedback

                def report(temps):
                    self.log({'event': 'startup_cooling', 'temperature_c': temps})
                    print('식히는 중 ' + json.dumps(temps), flush=True)

                sample = wait_until_cool(startup_sample, report,
                    sample_interval=CONFIG['temperature']['sample_interval_s'], clock=self.clock, pause=self.pause)
                self.startup_pending = False
            else:
                sample = self.feedback()
            self.cameras_stats.heartbeat(self.log)
            try:
                guarded = sample
                if self.temperature_fault:
                    guarded = {**sample, 'temperatures': {**sample['temperatures'], **self.temperature_fault},
                               'temperature_protection': {**sample['temperature_protection'], **self.temperature_fault_decisions}}
                base.feedback_gate(guarded)
                position = live_start.validate_position(sample, CONFIG['limits'])
                warnings = tuple(position['warnings'])
                if warnings != getattr(self,'position_warnings',()):
                    self.log(dict(event='observed_position_warning',**position))
                    self.position_warnings=warnings
                if self.motion_ready:
                    _, torque = self.read_modes()
                    sample['torque'] = torque
                    if any(value != 1 for value in torque.values()):
                        raise base.Stop('동작 중 토크 상태 변경: ' + repr(torque))
            except base.Stop:
                self.log({'event': 'stop_feedback', **sample,
                          'feedback_bytes_hex': {j:data.hex() for j,data in self.feedback_bytes.items()}})
                self.temperature_fault = {**(self.temperature_fault or {}),
                                          **{j: sample['temperatures'][j] for j, row in
                                             sample['temperature_protection'].items() if row['hold']}}
                raise
            return sample

        health_snapshot = checked

        def confirm_thermal_stop(self):
            if not self.temperature_fault or self.wire_fault:
                return
            bus = self.bus
            with self.thermal_diagnostics.recheck():
                for joint, trigger in self.temperature_fault.items():
                    trace, samples = [], []
                    try:
                        for _ in range(3):
                            values, _ = read_temperatures(bus,{joint:self.ids[joint]},{joint:trigger},StrictIndividualReplies,
                                traces=trace,last_requests=self.temperature_last_requests,
                                minimum_interval_s=CONFIG['temperature']['sample_interval_s'],clock=self.clock,pause=self.pause,
                                retry=self.communication)
                            samples.append(values[joint])
                        self.thermal_recheck[joint] = {'trigger_c':trigger,'register_63_reads_c':samples,'reply_trace':trace}
                    except Exception as error:
                        self.thermal_recheck[joint] = {'trigger_c':trigger,'read_error':str(error),'reply_trace':trace}
            self.log({'event':'thermal_recheck', **self.thermal_recheck})

        def release_if_supported_home(self, operator_supported):
            if not operator_supported or self.wire_fault:
                return 'NOT_AUTHORIZED_OR_WIRE_FAULT'
            sample = self.feedback()
            if any(abs(value-home)>1.0 for value,home in zip(sample['state'],self.context['home']['state'])):
                return 'NOT_AT_HOME_SUPPORT_REQUIRED'
            time.sleep(.25)
            parked = self.feedback()
            if any(abs(parked['present_raw'][j]-sample['present_raw'][j])>2 for j in base.JOINTS):
                return 'HOME_NOT_STATIONARY'
            bus = self.bus
            permission = self.permission
            original = permission.inspect
            sent = False
            owner = threading.get_ident()

            def inspect(port, packet):
                nonlocal sent
                if (not sent and port is permission.port and threading.get_ident() == owner
                        and not permission.closed and permission.phase == 'read' and torque_off_packet(bytes(packet))):
                    sent = True
                    self.torque_off_writes += 1
                    return 'torque_off'
                return original(port, packet)

            permission.inspect = inspect
            try:
                bus.sync_write('Torque_Enable', dict.fromkeys(base.JOINTS,0), normalize=False,num_retry=0)
                if not sent:
                    raise base.Stop('torque-off packet was not sent')
            finally:
                permission.inspect = original
            torque = {j: data[7] for j,data in self.read_block(33,8).items()}
            time.sleep(.25)
            released = self.feedback()
            drift = {j:released['present_raw'][j]-parked['present_raw'][j] for j in base.JOINTS}
            self.log({'event':'torque_off_readback', 'torque':torque, 'post_release_position_delta_ticks':drift})
            if any(torque.values()):
                return 'OFF_INCOMPLETE'
            return 'OFF_VERIFIED' if max(map(abs,drift.values())) <= 2 else 'OFF_VERIFIED_MOVEMENT_DETECTED'

        def connect_cameras(self):
            bus = self.bus
            if self.gripper_only:
                restrict_to_gripper(self.permission, lambda: self.expected_gripper)
            else:
                lo, hi = self.raw_bounds['elbow_flex']
                self.raw_bounds['elbow_flex'] = (lo, min(hi, ELBOW_INNER_LIMIT))
                a = self.normalize({j: self.raw_bounds[j][0] for j in base.JOINTS})
                b = self.normalize({j: self.raw_bounds[j][1] for j in base.JOINTS})
                self.bounds = [tuple(sorted((lo,hi))) for lo,hi in zip(a,b)]
            self.permission.READS = {**self.permission.READS, **dict(GRIPPER_REGS.values())}
            motor = bus.motors['gripper']
            with StrictIndividualReplies(bus.packet_handler,retry=self.communication):
                for register, location in GRIPPER_REGS.items():
                    if tuple(bus.model_ctrl_table[motor.model].get(register, ())) != location:
                        raise base.Stop(f'설치된 그리퍼 레지스터 주소 불일치 {register}')
                    self.registers[register] = bus.read(register, 'gripper', normalize=False, num_retry=0)
            self.log({'event': 'gripper_limits_readonly', **self.registers})
            self.open_cameras(self.checked)

        def open_cameras(self, check):
            self.camera_map = {}
            stats = self.cameras_stats

            class MeteredCamera(OpenCVCamera):
                def __init__(self, name, config):
                    self.role = name
                    super().__init__(config)

                def _postprocess_image(self, image, *args, **kwargs):
                    frame = super()._postprocess_image(image, *args, **kwargs)
                    stats.frame(self.role)
                    return frame

            for name, path in self.context['contract']['cameras'].items():
                camera = MeteredCamera(name, OpenCVCameraConfig(index_or_path=path, fps=30,
                    width=640, height=480, color_mode='rgb', warmup_s=CONFIG['startup']['warmup_s'], backend=200,
                    fourcc=CONFIG['cameras'][name]['fourcc']))
                self.cameras.append(camera)
                self.camera_map[name] = camera
                stats.started[name] = time.perf_counter()
                deadline = time.monotonic() + 5
                try:
                    wait(lambda: camera.connect(warmup=False), check, deadline=deadline, timeout=5)
                except Exception as exc:
                    raise base.Stop(f'{name} 카메라 열기 실패: {exc}') from exc
                while True:
                    with camera.frame_lock:
                        if camera.latest_frame is not None and time.monotonic() >= deadline - 2:
                            break
                    if time.monotonic() >= deadline:
                        raise base.Stop(f'{name} 카메라 첫 프레임 5초 초과')
                    check()
                    time.sleep(.05)

        def camera_frames(self):
            now = time.perf_counter()
            result = {}
            for name, camera in self.camera_map.items():
                with camera.frame_lock:
                    pixels, timestamp = camera.latest_frame, camera.latest_timestamp
                if pixels is None or timestamp is None or now - timestamp > 1:
                    return None
                # The producer replaces the frame reference instead of modifying the frame.
                result[name] = {'pixels': pixels, 'timestamp': timestamp}
            return result

        def fresh_frames(self):
            result = self.camera_frames()
            if result is None:
                return None
            brightness = {name:frame_brightness(frame['pixels']) for name,frame in result.items()}
            dark = {name for name,row in brightness.items() if min(row.values()) < 10}
            if dark:
                new_dark = dark-self.dark_cameras
                if new_dark:
                    from PIL import Image
                    for name in new_dark:
                        Image.fromarray(result[name]['pixels']).save(self.out/f'dark_{name}.png')
                    self.log({'event':'camera_near_black', 'cameras':sorted(dark), 'brightness':brightness})
                    print('카메라 영상이 거의 검습니다: ' + ', '.join(sorted(dark)),flush=True)
                self.dark_cameras = dark
                return None
            self.dark_cameras.clear()
            if not self.saved_first_frames:
                from PIL import Image
                from frame_compare import compare
                for name, frame in result.items():
                    Image.fromarray(frame['pixels']).save(self.out/f'live_{name}.png')
                self.saved_first_frames = True
                training = Path(__file__).resolve().parents[1]/'analysis/vla_v3_blue_eval_preview'
                if all((training/f'training_{name}.png').is_file() for name in result):
                    compare(training, self.out, self.out/'camera_comparison.png')
            return result

        def correction_frames(self):
            from corrections import CAMERAS

            result = {}
            for role in CAMERAS:
                camera = self.camera_map[role]
                with camera.frame_lock:
                    pixels, captured = camera.latest_frame, camera.latest_timestamp
                if pixels is None or captured is None or pixels.shape != (480, 640, 3):
                    return None
                result[role] = {'pixels': pixels, 'timestamp': captured}
            timestamps = [frame['timestamp'] for frame in result.values()]
            now = time.perf_counter()
            if now - min(timestamps) > 0.15 or max(timestamps) - min(timestamps) > 0.1:
                return None
            if any(min(frame_brightness(frame['pixels']).values()) < 10 for frame in result.values()):
                return None
            return result

        def send(self, raw):
            return self._send_policy(raw)

        def _send_gripper_once(self,raw):
            low, high = self.raw_bounds['gripper']
            target = raw['gripper']
            if type(target) is not int or not low <= target <= high:
                raise base.Stop('그리퍼 정수 목표 범위 오류')
            sent = {'gripper': target}
            self.expected_gripper = target
            self.goal_attempted = True
            try:
                with self.permission.goal_once():
                    self.bus.sync_write('Goal_Position', sent, normalize=False, num_retry=0)
            except Exception as error:
                retry=retryable_write_error(error,dict(register='Goal_Position',goals=sent))
                if retry is not None:raise retry from error
                self.wire_fault=True;raise
            finally:
                self.expected_gripper = None
            return sent

        def hold(self):
            return self._hold_policy()

        def close(self):
            try:
                super().close()
                if self.robot is None:
                    for camera in reversed(self.cameras):
                        try:
                            if camera.is_connected or camera.thread is not None:
                                camera.disconnect()
                        except Exception as exc:
                            self.cleanup_errors.append(f'{camera.role} 카메라 종료 실패: {exc}')
            finally:
                self.camera_logger.removeHandler(self.cameras_stats)
                self.communication.detach()

    return StreamingIO


def make_policy(context):
    import numpy as np
    from stream_policy import StreamingPolicy

    policy = StreamingPolicy(context, base.TASK, rtc=False)
    try:
        frames = {name: {'pixels': np.zeros((480, 640, 3), dtype=np.uint8)}
                  for name in context['contract']['cameras']}
        state = context['home']['state']
        first, seconds1 = policy(frames, state, 0, 0)
        second, seconds2 = policy(frames, state, 20, 20)
        base.chunk(first)
        base.chunk(second)
        policy.reset()
        policy.warm_rtc_estimate = seconds2
        print(json.dumps({'rtc': policy.transport.config['stream_rtc'],
                          'warmup_s': [seconds1, seconds2]}), flush=True)
        return policy
    except BaseException:
        policy.close()
        raise


def run_gripper_test(io, monitor, corrections, deadline, log):
    sample = io.checked()
    for name, (low, high) in io.raw_bounds.items():
        current = sample['present_raw'][name]
        if not low <= current <= high:
            raise base.Stop(f'그리퍼 시험 시작 자세가 범위 밖입니다: {name}={current} (허용 {low}–{high})')
    print('관제 화면에서 그리퍼 열어 입력 대기. 요청이 없으면 45초 이내 종료합니다.', flush=True)
    outcomes, hold = corrections.session(io, monitor, None, None, deadline, log, one_shot=True)
    final = outcomes[-1] if outcomes else {}
    clip = monitor.latest_clip
    success = (final.get('status') == 'completed' and clip is not None
               and hold['status'] in ('CURRENT_POSITION_GOAL_TRANSMITTED','HOLD_CONFIRMED'))
    reason = None if success else (final.get('recording_error') or final.get('reason')
                                   or hold.get('reason') or '그리퍼 교정 또는 녹화가 완료되지 않았습니다')
    return {'status': 'RUN_COMPLETE' if success else 'HOLD', 'reason': reason,
            'hold': hold, 'corrections': outcomes, 'clip': clip,
            'steps': clip['frames'] if clip else 0}


def start_policy_session(io, policy, home, log, duration, speed, monitor=None, allow_corrections=False, *, motion_options=None):
    summary = live_start.prepare(io, home, log)
    io.motion_ready = True
    print('시작 직전 ' + json.dumps(summary, ensure_ascii=False), flush=True)
    return stream(io, policy, log, duration, speed, monitor, allow_corrections,
                  **({'motion_options': motion_options} if motion_options else {}))


def live(duration, supported_home=False, speed=DEFAULT_SPEED, web_corrections=False,
         taught_forward=None, gripper_test=False, *, assist_grasp=False, vel_limit=None):
    from trial_runtime import static_context
    from run_exit_report import record_run_directory
    import corrections

    options = load_options(assist_grasp, vel_limit, speed)
    if gripper_test and options:
        raise ValueError('legacy motion options require a policy session')
    context = static_context()
    forward = corrections.load_forward_profile(taught_forward, context['calibration']) if taught_forward else None
    kinds = (['gripper_open', 'forward'] if forward else ['gripper_open']) if web_corrections or gripper_test else []
    out = Path(__file__).resolve().parents[1] / 'runs' / time.strftime('%Y%m%d_%H%M%S')
    if out.exists():out=out.with_name(out.name+'_'+str(time.time_ns()))
    out.mkdir()
    record_run_directory(out)
    result = {'status': 'HOLD', 'steps': 0, 'task_success': 'UNVERIFIED',
              'speed': speed, 'playback_hz': MODEL_HZ * speed}
    if options:
        result['motion_options'] = options.metadata()
    io = policy = monitor = None
    stats = {'relief':0,'startup':[]}
    with (out / 'steps.jsonl').open('x', encoding='utf-8', buffering=1) as trace:
        def log(row):
            if row.get('event')=='goal_transmitted':stats['relief']+=int(row.get('grip_relief',False))
            if row.get('event','').startswith('startup_'):stats['startup'].append(row)
            trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')

        try:
            task = 'Open the gripper' if gripper_test else base.TASK
            monitor = Monitor(out.name, task, base.JOINTS, correction_kinds=kinds)
            if not gripper_test:
                print(f'재생 속도 {speed:g}배, 초당 모델 액션 {MODEL_HZ*speed:g}개', flush=True)
                monitor.status('warming', force=True)
                stage_started = time.monotonic()
                log(dict(event='startup_model_begin', unix=time.time()))
                policy = make_policy(context)
                log(dict(event='startup_model_complete', seconds=time.monotonic()-stage_started, unix=time.time()))
            monitor.status('connecting', force=True)
            io = device_class()(context, log, out, gripper_only=gripper_test)
            stage_started = time.monotonic()
            io.connect()
            log(dict(event='startup_devices_complete', seconds=time.monotonic()-stage_started, unix=time.time()))
            preview = io.camera_frames()
            if preview is not None:
                sample = io.checked()
                monitor.capture(preview, sample['state'], preview=True)
                monitor.status('connecting', sample=sample, force=True)
            log({'event': 'start', 'duration_s': duration, 'speed': speed, 'playback_hz': MODEL_HZ * speed,
                 'raw_bounds': io.raw_bounds,
                 'device_preflight': io.preflight, 'prior_stop_inventory': context['stop_inventory']})
            deadline = time.monotonic() + duration
            if gripper_test:
                result = run_gripper_test(io, monitor, corrections, deadline, log)
            else:
                result = start_policy_session(io, policy, context['home']['state'], log, duration, speed,
                                              monitor, allow_corrections=web_corrections,
                                              **({'motion_options': options} if options else {}))
                request = result.pop('operator_request')
                if request is not None:
                    if result['hold']['status'] == 'HOLD_NOT_CONFIRMED':
                        raise base.Stop('기존 정책 목표를 정지하지 못해 교정하지 않았습니다')
                    result.update(status='HOLD', reason='운영자 교정으로 정책 실행 종료')
                    result['corrections'], result['hold'] = corrections.session(
                        io, monitor, request, forward,
                        min(result['ready']['monotonic_s']+duration, time.monotonic() + 30), log)
            result['torque_off'] = 'TORQUE_RETAINED'
            if (supported_home and (result['status'] == 'RUN_COMPLETE' or result['reason'] == 'Ctrl+C')
                    and result['hold']['status'] in ('CURRENT_POSITION_GOAL_TRANSMITTED','HOLD_CONFIRMED')):
                try:
                    sample = io.start_snapshot()
                    moved = live_start.return_home(io, sample, context['home']['state'], 1.0)
                    result['park'] = 'RETURNED_HOME' if moved else 'ALREADY_AT_HOME'
                    result['torque_off'] = io.release_if_supported_home(True)
                except Exception as exc:
                    result.update(park='RELEASE_NOT_COMPLETED', release_error=str(exc))
        except (KeyboardInterrupt, Exception) as exc:
            if io is not None and (io.goal_attempted or getattr(io,'gripper_overload_fault',None) is not None):
                result['hold']=base.try_hold(io)
            result['reason'] = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
            if isinstance(exc, KeyboardInterrupt):
                result['status'] = 'STOPPED'
        finally:
            if io is not None:
                result.setdefault('gripper_peak_c', io.gripper_peak)
                result['peak_temperatures_c'] = dict(io.peak_temperatures)
                result.setdefault('camera', io.cameras_stats.summary())
                result['gripper_limits_readonly'] = io.registers
                result['thermal_recheck'] = io.thermal_recheck
                result['thermal_diagnostics'] = io.thermal_diagnostics.summary()
                result['thermal_protection'] = io.thermal_window.summary()
                result['communication'] = io.communication.summary()
                result['thermal_confirmation'] = dict(policy=CONFIG['temperature']['policy'],
                    counts=io.thermal_confirmation_counts)
                result['startup_temperatures_c'] = getattr(io,'initial_temperatures',{})
                result['goal_packet_attempts'] = getattr(getattr(io, 'permission', None), 'goal_packets', 0)
                try:
                    io.close()
                except Exception as exc:
                    result['cleanup_exception'] = str(exc)
                serial = getattr(getattr(getattr(io, 'bus', None), 'port_handler', None), 'ser', None)
                result.update(port_closed=not bool(getattr(serial, 'is_open', False)), cleanup_errors=io.cleanup_errors)
            if policy is not None:
                try:
                    policy.close()
                except Exception as exc:
                    result['policy_cleanup_exception'] = str(exc)
            result['gripper_relief_count']=stats['relief']
            result.setdefault('gripper_close_wait', GripperArrivalGate().summary())
            result['startup_auto_processed']=stats['startup']
            result.setdefault('gripper_opening_protection',dict(open_cap_points=CONFIG['gripper']['max_open_points'],policy_open_cap_clips=0,opening_load_freezes=0))
            result['gripper_open_clip_count']=result['gripper_opening_protection']['policy_open_cap_clips']
            result.setdefault('lateral_correction',dict(enabled=CONFIG['lateral_correction']['enabled'],configured_offset_deg=CONFIG['lateral_correction']['offset_deg'],applied_steps=0,peak_abs_deg=0.))
            result['gripper_overload_recovery']=getattr(io,'gripper_overload_recovery',None)
            result.update(output=str(out), torque_register_writes=(io.torque_off_writes + io.torque_on_writes) if io is not None else 0,
                          configuration_register_writes=0)
            if monitor is not None:
                monitor.finish(result['status'], result['reason'], result['steps'], result.get('hold'))
            (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result.get('port_closed') or any(result.get(key) for key in
                                           ('cleanup_errors', 'cleanup_exception', 'policy_cleanup_exception')):
        return 1
    return {'RUN_COMPLETE': 0, 'STOPPED': 130}.get(result['status'], 1)


class StreamingTests(unittest.TestCase):
    def test_startup_cases_share_policy_loop(self):
        from startup_simulation import SCENARIOS, run_case

        def policy(frames, state, origin, delay):
            return [state[:] for _ in range(50)], .05

        for case in SCENARIOS:
            with self.subTest(case=case):
                result = run_case(case, 14, DEFAULT_SPEED, CONFIG['home']['state'], {'sample': True}, policy)
                self.assertTrue(result['passed'], result)

    def test_invalid_position_and_mode_prevent_all_writes(self):
        from startup_simulation import SimulatedArm

        io = SimulatedArm(CONFIG['home']['state'], {}, lambda _: None, 'all_off')
        io.present['elbow_flex'] = io.target['elbow_flex'] = 5000
        with self.assertRaisesRegex(base.Stop, 'elbow_flex'):
            live_start.prepare(io, io.home, lambda _: None)
        self.assertEqual(io.start_writes, [])
        io = SimulatedArm(CONFIG['home']['state'], {}, lambda _: None, 'all_off')
        original_snapshot = io.start_snapshot
        def invalid_mode():
            sample = original_snapshot()
            sample['modes']['elbow_flex'] = 2
            return sample
        io.start_snapshot = invalid_mode
        with self.assertRaisesRegex(base.Stop, '위치 모드'):
            live_start.prepare(io, io.home, lambda _: None)
        self.assertEqual(io.packet_count, 0)

    def test_startup_packet_targets_only_requested_axes_and_register(self):
        self.assertEqual(live_start.exact_sync_packet(42, 2, {6: 0x102}), bytes.fromhex('fffffe07832a0206020142'))
        self.assertEqual(live_start.exact_sync_packet(40, 1, {5: 1}), bytes.fromhex('fffffe06832801050149'))

    def test_grip_relief(self):
        relief = GripRelief()
        self.assertEqual(relief.apply(11, 30, 0), 11)
        self.assertEqual(relief.apply(11, 29.5, 0.49), 11)
        self.assertEqual(relief.apply(11, 29.5, 0.5), 25.5)
        self.assertEqual(relief.apply(11, 27, 0.7), 25.5)
        # A newly opening command releases relief even before it exceeds position.
        self.assertEqual(relief.apply(18, 27, 0.8), 18)
        self.assertIsNone(relief.cap)

    def test_recorded_opening_direction_is_never_closing_relief(self):
        relief = GripRelief()
        for seconds in (0, .5, 1, 2):
            self.assertEqual(relief.apply(47.613, 1.163, seconds), 47.613)
        self.assertIsNone(relief.cap)

    def test_moving_closure_does_not_count_as_obstruction(self):
        relief = GripRelief()
        for seconds, position in ((0, 40), (.5, 37), (1, 34), (1.5, 31)):
            self.assertEqual(relief.apply(0, position, seconds), 0)
        self.assertIsNone(relief.cap)

    def test_web_open_and_relief_use_the_same_recorded_direction(self):
        from corrections import target_for
        from types import SimpleNamespace
        sample = {'state': [0, 0, 0, 0, 0, 30]}
        target, changed = target_for(SimpleNamespace(bounds=[(-180, 180)] * 5 + [(0, 100)]),
                                     sample, 'gripper_open', None)
        self.assertEqual(changed, (5,))
        self.assertEqual(target[-1], 33)
        self.assertEqual(target[:5], sample['state'][:5])
        relief = GripRelief()
        self.assertEqual(relief.apply(0, 30, 0), 0)
        self.assertEqual(relief.apply(0, 30, .5), 26)
        self.assertEqual(relief.apply(target[-1], 30, .6), 33)
        self.assertIsNone(relief.cap)

    def test_aligned_overlap_only_affects_unsent_slots(self):
        queue = {index: [0] for index in range(50)}
        self.assertEqual(merge(queue, [[100]]*50, 20, 40), 10)
        self.assertEqual(queue[39], [0])
        self.assertAlmostEqual(queue[40][0], 10)
        self.assertAlmostEqual(queue[49][0], 100)
        self.assertEqual(queue[50], [100])
        self.assertEqual(merge(queue, [[99]]*50, 0, 70), 0)

    def test_interpolation_and_scaled_chunk_timing(self):
        self.assertEqual(interpolate([0, 5], [4, 1], 0.5), [2, 3])
        self.assertEqual(interpolate([4], [9], 1), [9])
        rate = MODEL_HZ * DEFAULT_SPEED
        self.assertAlmostEqual(rate, 9.9)
        self.assertFalse(infer_due(30, 49, rate, 1.4))
        self.assertTrue(infer_due(31, 49, rate, 1.4))
        self.assertEqual(chunk_alignment(31, 1.4, rate, 44, moving=True), (13,44))
        self.assertEqual(chunk_alignment(31, 1.4, rate, 45, moving=True), (14,45))
        self.assertEqual(chunk_alignment(50, 1.4, rate, 50, moving=False), (0,50))

    def test_startup_cooling_and_exact_torque_off_packet(self):
        samples = iter([50, 49, 45, 44])
        waits, seen = [], []
        sample = wait_until_cool(lambda:{'temperatures':{'lift':next(samples)}},seen.append,pause=waits.append)
        self.assertEqual(sample['temperatures']['lift'],44)
        self.assertEqual(waits,[10,10,10])
        self.assertEqual([x['lift'] for x in seen],[50,49,45])
        packet = bytearray([255,255,254,16,131,40,1]+[v for j in range(1,7) for v in (j,0)])
        packet.append((~sum(packet[2:])) & 255)
        self.assertTrue(torque_off_packet(packet))
        packet[8] = 1
        packet[-1] = (~sum(packet[2:-1])) & 255
        self.assertFalse(torque_off_packet(packet))

    class FakeIO:
        goal_attempted = False
        gripper_peak = 35
        peak_temperatures = dict.fromkeys(base.JOINTS, 35)
        cameras_stats = CameraStats({})

        def __init__(self, read_delay=0):
            self.read_delay = read_delay
            self.writes = []

        def checked(self):
            if self.read_delay:
                time.sleep(self.read_delay)
            return {'state': [0]*6}

        def fresh_frames(self):
            return {}

        def plan(self, target):
            return {'wire_raw': tuple(round(x) for x in target), 'wire_normalized': target}

        def send(self, raw):
            self.goal_attempted = True
            self.writes.append(raw)

        def hold(self):
            pass

    @staticmethod
    def fake_policy(frames, state, origin, delay):
        return [[origin+step, 0, 0, 0, 0, 0] for step in range(50)], 0.025

    def test_slow_playback_preserves_interpolated_packet_cadence(self):
        io = self.FakeIO()
        rows = []
        result = stream(io, self.fake_policy, rows.append, 0.4)
        packets = [row for row in rows if row['event']=='goal_transmitted']
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertEqual(result['speed'], 0.33)
        self.assertEqual(result['final_rate_target'], 60)
        self.assertGreaterEqual(len(packets), 10)
        self.assertGreater(len(io.writes), len(set(io.writes)))
        self.assertTrue(any(not row['model'][0].is_integer() for row in packets))
        elapsed = packets[-1]['time_s'] - packets[0]['time_s']
        self.assertAlmostEqual(packets[-1]['model_phase'] / elapsed, 9.9, delta=0.3)
        self.assertEqual(result['peak_temperatures_c'], dict.fromkeys(base.JOINTS, 35))
        self.assertEqual(result['chunk_wait_s'], {'max': 0, 'sum': 0})

    def test_slow_bus_retains_existing_fallback(self):
        io = self.FakeIO(read_delay=0.017)
        rows = []
        result = stream(io, self.fake_policy, rows.append, 0.3, speed=0.5)
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertEqual(result['playback_hz'], 15)
        self.assertEqual(result['final_rate_target'], 50)
        self.assertTrue(any(row['event'] == 'cadence_fallback' for row in rows))

    def test_operator_hold_cancels_remaining_goals(self):
        io = self.FakeIO()

        class OperatorMonitor:
            def hold_requested(self):
                return bool(io.writes)

            def capture(self, frames, state):
                pass

            def status(self, phase, **state):
                pass

        result = stream(io, self.fake_policy, lambda row: None, 0.4, monitor=OperatorMonitor())
        self.assertEqual(result['reason'], VIEWER_STOP)
        self.assertEqual(result['steps'], 1)
        self.assertEqual(len(io.writes), 1)
        self.assertEqual(result['hold']['status'], 'CURRENT_POSITION_GOAL_TRANSMITTED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--mock', action='store_true')
    mode.add_argument('--policy-check', action='store_true')
    mode.add_argument('--simulate', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--gripper-test', action='store_true',
                      help='카메라를 연결하고 그리퍼 열기 요청 1회만 기다림')
    parser.add_argument('--duration', type=float, default=180)
    parser.add_argument('--speed', type=float, default=DEFAULT_SPEED,
                        help='모델 액션 재생 배율 (기본 0.33, 0 < speed <= 1)')
    parser.add_argument('--release', '--supported-home', dest='supported_home', action='store_true',
                        help='home에서 전원이 꺼져도 팔을 받치는 물리 지지대를 설치한 경우만 사용')
    parser.add_argument('--web-corrections', action='store_true',
                        help='현장 운영자가 감독하는 동안 제한된 관제 교정을 허용')
    parser.add_argument('--taught-forward', type=Path,
                        help='현장에서 시연해 확인한 앞으로 경로 파일')
    args = parser.parse_args()
    if args.mock:
        unittest.main(argv=[__file__], verbosity=2)
    if args.policy_check:
        from trial_runtime import static_context
        make_policy(static_context()).close()
        raise SystemExit(0)
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error('--duration은 0보다 큰 유한한 초 단위 값이어야 합니다.')
    if not 0 < args.speed <= 1:
        parser.error('--speed는 0보다 크고 1 이하인 유한한 배율이어야 합니다.')
    if args.dry_run or args.simulate:
        from startup_simulation import run_dry, run_simulations
        if args.simulate:
            raise SystemExit(run_simulations(args.duration, args.speed))
        raise SystemExit(run_dry(args.duration, args.speed))
    if args.taught_forward and not args.web_corrections:
        parser.error('--taught-forward에는 --web-corrections가 필요합니다.')
    if args.gripper_test and (args.web_corrections or args.taught_forward or args.supported_home):
        parser.error('--gripper-test는 그리퍼 단독 시험 옵션입니다.')
    if os.environ.get('SO101_DIRECT_OPERATOR') != '1' or not os.isatty(0):
        parser.error('현장 Jetson 터미널에서 run.sh를 사용하세요.')
    duration = min(args.duration, 45) if args.gripper_test else args.duration
    raise SystemExit(live(duration, args.supported_home, args.speed, args.web_corrections,
                          args.taught_forward, args.gripper_test))
