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
from vla_monitor import Monitor

from config_runtime import load as load_config
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


def merge(queue, incoming, origin, first_unused):
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
        queue[index] = blend(queue[index], target, (k + 1) / overlap) if k < overlap else target
    return overlap


def interpolate(a, b, fraction):
    # Cubic with zero slope at the original action knots. Never overshoots endpoints.
    return blend(a, b, fraction * fraction * (3 - 2 * fraction))


def infer_due(phase, buffer_end, rate, estimated_seconds):
    remaining_seconds = max(0.0, buffer_end - phase) / rate
    return remaining_seconds <= estimated_seconds * LOOKAHEAD


def chunk_alignment(origin, elapsed, rate, first_unused, *, moving):
    expired = math.floor(elapsed * rate) if moving else 0
    return expired, max(first_unused, origin + expired)


def wait_until_cool(read, report, *, pause=time.sleep):
    sample = read()
    if max(sample['temperatures'].values()) < 50:
        return sample
    while max(sample['temperatures'].values()) >= 45:
        report(sample['temperatures'])
        pause(10)
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


def stream(io, policy, log, duration, speed=DEFAULT_SPEED, monitor=None, allow_corrections=False):
    clock = getattr(io, 'clock', time.monotonic)
    pause = getattr(io, 'pause', time.sleep)
    started = clock()
    deadline = started + duration
    rate = MODEL_HZ * speed
    slots = {}
    phase = 0.0
    sent_phase = None
    previous_tick = None
    next_tick = started
    last_send = None
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
    correction = None
    observed = [[] for _ in base.JOINTS]
    commands = [[] for _ in base.JOINTS]
    relief = GripRelief()
    pool_factory = getattr(io, 'pool_factory', concurrent.futures.ThreadPoolExecutor)
    pool = pool_factory(max_workers=1)
    try:
        while count < max_steps:
            pause(max(0, min(deadline, next_tick) - clock()))
            tick_started = clock()
            if tick_started >= deadline:
                break
            if monitor is not None and monitor.hold_requested():
                raise base.Stop(VIEWER_STOP)
            if allow_corrections:
                correction = monitor.take_correction()
                if correction is not None:
                    break
            next_tick = tick_started + 1 / hz
            sample = io.checked()
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
            if job is not None and job.done():
                actions, seconds = job.result()
                inferences.append(seconds)
                estimate = max(seconds, estimate * 0.85)
                incoming = base.chunk(actions)
                expired, usable = chunk_alignment(origin, seconds, request_rate, first_unused, moving=request_moving)
                skip = usable - origin
                # An expired chunk cannot bridge the current playback cursor. Refresh
                # from the held pose if the existing buffer runs out.
                discarded = skip >= len(incoming) or usable > buffer_end + 1
                overlap = 0 if discarded else merge(slots, incoming, origin, usable)
                blend_delta = [max((abs(slots[k][i] - incoming[k-origin][i])
                                    for k in range(usable, usable+overlap)), default=0) for i in range(6)]
                log({'event': 'inference', 'origin': origin, 'seconds': seconds,
                     'overlap': overlap, 'overlap_playback_s': overlap / MODEL_HZ,
                     'overlap_wall_s': overlap / rate, 'playback_hz': rate,
                     'elapsed_speed_actions': seconds * rate, 'expired_actions': expired,
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
                frames = io.fresh_frames()
                if frames is not None:
                    missing_since = None
                    requested = clock()
                    origin = first_unused
                    last_origin = origin
                    request_moving = sent_phase is not None and buffer_end > phase
                    request_rate = rate
                    delay = min(remaining, math.ceil(rate * estimate)) if request_moving else 0
                    job = pool.submit(policy, frames, sample['state'], origin, delay)
                    if monitor is not None:
                        monitor.capture(frames, sample['state'])
                elif missing_since is None:
                    missing_since = tick_started
                elif tick_started - missing_since > 8:
                    raise base.Stop('새 카메라 프레임을 받지 못해 추론 중단')
            lower, upper = math.floor(phase), math.ceil(phase)
            if lower not in slots or upper not in slots or (phase == sent_phase and phase >= buffer_end):
                if last_send is not None and underrun is None:
                    underrun = tick_started
                if monitor is not None:
                    monitor.status('predicting' if job is not None else 'observing', step=count,
                                   sample=sample, model=last_model, sent=last_wire,
                                   inference_seconds=inferences[-1] if inferences else None)
                continue
            model = interpolate(slots[lower], slots[upper], phase - lower)
            target = model.copy()
            target[-1] = relief.apply(target[-1], sample['state'][-1], phase / MODEL_HZ)
            plan = io.plan(target)
            prepared = clock()
            if last_send is not None:
                pause(max(0, min(deadline, last_send + 1 / hz) - prepared))
            send_started = clock()
            if send_started >= deadline:
                break
            if monitor is not None and monitor.hold_requested():
                raise base.Stop(VIEWER_STOP)
            if allow_corrections:
                correction = monitor.take_correction()
                if correction is not None:
                    break
            # Repeat a quantized target when the interpolated motion is smaller
            # than one motor tick. Model speed must not lower the packet cadence.
            io.send(plan['wire_raw'])
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
            log({'event': 'goal_transmitted', 'step': count + 1, 'time_s': sent-started,
                 'gap_s': gap, 'hz_target': hz, 'loop_work_s': loop_work,
                 'model_phase': phase, 'playback_s': phase / MODEL_HZ,
                 'feedback': sample, 'model': model,
                 'grip_relief': target[-1] != model[-1], **plan})
            count += 1
            last_send = sent
            last_model, last_wire = model, plan['wire_normalized']
            if monitor is not None:
                monitor.status('moving', step=count, sample=sample, model=last_model, sent=last_wire,
                               inference_seconds=inferences[-1] if inferences else None)
            if hz == CONTROL_HZ and loop_work > 0.016:
                hz = 50
                next_tick = tick_started + 1 / hz
                log({'event': 'cadence_fallback', 'loop_work_s': loop_work, 'hz_target': hz})
    except (KeyboardInterrupt, Exception) as exc:
        reason = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
    finally:
        if io.goal_attempted:
            hold = base.try_hold(io)
            if hasattr(io, 'confirm_thermal_stop'):
                io.confirm_thermal_stop()
        pool.shutdown(wait=False, cancel_futures=True)
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
            'steps': count, 'step_limit': max_steps, 'elapsed_s': clock() - started,
            'task_success': 'UNVERIFIED',
            'speed': speed, 'playback_hz': rate, 'playback_s': phase / MODEL_HZ,
            'gripper_peak_c': io.gripper_peak, 'peak_temperatures_c': dict(io.peak_temperatures),
            'camera': io.cameras_stats.summary(),
            'inference_s': {'mean': statistics.mean(inferences) if inferences else None, 'samples': inferences},
            'chunk_wait_s': {'max': max(waits, default=0), 'sum': sum(waits)},
            'transmission_hz': 1/statistics.mean(periods) if periods else None, 'final_rate_target': hz,
            'tracking': tracking}


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
            self.torque_off_writes = 0
            self.torque_on_writes = 0
            self.startup_pending = True
            self.motion_ready = False
            self.out = out
            self.saved_first_frames = False
            self.dark_cameras = set()
            self.registers = {}
            self.cameras_stats = CameraStats(context['contract']['cameras'])
            self.camera_logger = logging.getLogger('lerobot.cameras.opencv.camera_opencv')
            self.camera_logger.addHandler(self.cameras_stats)

        def check_start_mode(self):
            if self.gripper_only:
                return super().check_start_mode()
            self.raw_bounds = {j:tuple(CONFIG['limits'][j]) for j in base.JOINTS}
            self.arm_bounds = self.raw_bounds.copy()
            self.permission.READS = {**self.permission.READS, 67: 2}
            self.read_modes()

        def read_modes(self):
            block = self.read_block(33, 8)
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
            if self.gripper_only or self.wire_fault or not goals or not goals.keys() <= self.ids.keys():
                raise base.Stop('시작 패킷 범위 위반')
            if register == 'Goal_Position' and all(type(value) is int and self.arm_bounds[j][0] <= value <= self.arm_bounds[j][1]
                                                  for j, value in goals.items()):
                address, width = 42, 2
            elif register == 'Torque_Enable' and all(type(value) is int and value == 1 for value in goals.values()):
                address, width = 40, 1
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
                        self.torque_on_writes += 1
                        return 'torque_on'
                    return 'goal'
                return original(port, packet)

            permission.inspect = inspect
            try:
                self.bus.sync_write(register, goals, normalize=False, num_retry=0)
                if not sent:
                    raise base.Stop('검증된 시작 패킷을 보내지 못했습니다')
                self.log({'event': 'startup_write', 'register': register, 'values': goals})
            except BaseException:
                self.wire_fault = True
                raise
            finally:
                permission.inspect = original

        def read_block(self, address, width):
            data = super().read_block(address, width)
            if address == 56:
                self.feedback_bytes = data
            return data

        def feedback(self):
            sample = super().feedback()
            sample['temperatures_bulk'] = dict(sample['temperatures'])
            try:
                sample['temperatures'], disagreement = read_temperatures(
                    self.bus, self.ids, sample['temperatures_bulk'], StrictIndividualReplies)
            except Exception as exc:
                self.wire_fault = True
                raise base.Stop('개별 온도 응답 검증 실패; 추가 쓰기 금지: ' + str(exc)) from exc
            if disagreement != self.thermal_disagreement:
                if disagreement:
                    self.log({'event': 'thermal_source_disagreement', 'joints': disagreement})
                self.thermal_disagreement = disagreement
            sample['current_raw'] = {}
            for name, mid in self.ids.items():
                value = int.from_bytes(self.feedback_bytes[name][13:15], 'little')
                sample['current_raw'][name] = self.bus._decode_sign('Present_Current', {mid: value})[mid]
            if not hasattr(self,'initial_temperatures'):self.initial_temperatures=dict(sample['temperatures'])
            temperature = sample['temperatures']['gripper']
            for joint, value in sample['temperatures'].items():
                self.peak_temperatures[joint] = max(value, self.peak_temperatures.get(joint, value))
            if self.gripper_peak is None:
                self.log({'event': 'startup_temperatures', 'temperature_c': sample['temperatures']})
                print('시작 온도(°C) ' + json.dumps(sample['temperatures']), flush=True)
                if temperature >= 45:
                    print(f'그리퍼 시작 온도 경고 {temperature}°C', flush=True)
            self.gripper_peak = max(self.gripper_peak or temperature, temperature)
            if self.motion_ready:
                hot = {joint: value for joint, value in sample['temperatures'].items() if value >= 55}
                if hot:
                    self.temperature_fault = {**(self.temperature_fault or {}), **hot}
            # Collection must remain available to the existing current-position HOLD.
            return sample

        def checked(self):
            if self.startup_pending:
                def startup_sample():
                    feedback = self.feedback()
                    if any(feedback['statuses'].values()):
                        base.feedback_gate(feedback)
                    return feedback

                def report(temps):
                    self.log({'event': 'startup_cooling', 'temperature_c': temps})
                    print('식히는 중 ' + json.dumps(temps), flush=True)

                sample = wait_until_cool(startup_sample, report)
                self.startup_pending = False
            else:
                sample = self.feedback()
            self.cameras_stats.heartbeat(self.log)
            try:
                guarded = sample
                if self.temperature_fault:
                    guarded = {**sample, 'temperatures': {**sample['temperatures'], **self.temperature_fault}}
                base.feedback_gate(guarded)
                if self.motion_ready:
                    _, torque = self.read_modes()
                    if any(value != 1 for value in torque.values()):
                        raise base.Stop('동작 중 토크 상태 변경: ' + repr(torque))
            except base.Stop:
                self.log({'event': 'stop_feedback', **sample,
                          'feedback_bytes_hex': {j:data.hex() for j,data in self.feedback_bytes.items()}})
                self.temperature_fault = {**(self.temperature_fault or {}),
                                          **{j: v for j, v in sample['temperatures'].items() if v >= 55}}
                raise
            return sample

        health_snapshot = checked

        def confirm_thermal_stop(self):
            if not self.temperature_fault or self.wire_fault:
                return
            bus = self.bus
            with StrictIndividualReplies(bus.packet_handler):
                for joint, trigger in self.temperature_fault.items():
                    try:
                        samples = [bus.read('Present_Temperature',joint,normalize=False,num_retry=0) for _ in range(3)]
                        self.thermal_recheck[joint] = {'trigger_c':trigger, 'register_63_reads_c':samples}
                    except Exception as error:
                        self.thermal_recheck[joint] = {'trigger_c':trigger, 'read_error':str(error)}
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
            with StrictIndividualReplies(bus.packet_handler):
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
                result[name] = {'pixels': pixels}
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
                training = Path.home()/'vla_v3_blue_eval_preview'
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
            if not self.gripper_only:
                return super().send(raw)
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
            except BaseException:
                self.wire_fault = True
                raise
            finally:
                self.expected_gripper = None
            return sent

        def hold(self):
            if self.gripper_only:
                return super().hold()
            if self.wire_fault:
                raise base.Stop('직전 송신 결과가 불확실하여 추가 쓰기 금지')
            current = self.feedback()['present_raw']
            _, torque = self.read_modes()
            live_start.validate_position({'present_raw': current}, self.arm_bounds)
            raw = {joint: min(value, self.arm_bounds[joint][1]) for joint, value in current.items() if torque[joint] == 1}
            if not raw:
                raise base.Stop('토크가 꺼져 있어 유지 목표를 전송하지 않았습니다')
            self.write_start('Goal_Position', raw)
            if len(raw) != len(base.JOINTS):
                raise base.Stop('토크가 켜진 축에만 유지 목표를 전송했습니다')

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
               and hold['status'] == 'CURRENT_POSITION_GOAL_TRANSMITTED')
    reason = None if success else (final.get('recording_error') or final.get('reason')
                                   or hold.get('reason') or '그리퍼 교정 또는 녹화가 완료되지 않았습니다')
    return {'status': 'RUN_COMPLETE' if success else 'HOLD', 'reason': reason,
            'hold': hold, 'corrections': outcomes, 'clip': clip,
            'steps': clip['frames'] if clip else 0}


def start_policy_session(io, policy, home, log, duration, speed, monitor=None, allow_corrections=False):
    summary = live_start.prepare(io, home, log)
    io.motion_ready = True
    print('시작 직전 ' + json.dumps(summary, ensure_ascii=False), flush=True)
    return stream(io, policy, log, duration, speed, monitor, allow_corrections)


def live(duration, supported_home=False, speed=DEFAULT_SPEED, web_corrections=False,
         taught_forward=None, gripper_test=False):
    from trial_runtime import static_context
    import corrections

    context = static_context()
    forward = corrections.load_forward_profile(taught_forward, context['calibration']) if taught_forward else None
    kinds = (['gripper_open', 'forward'] if forward else ['gripper_open']) if web_corrections or gripper_test else []
    out = Path(__file__).resolve().parents[1] / 'runs' / time.strftime('%Y%m%d_%H%M%S')
    if out.exists():out=out.with_name(out.name+'_'+str(time.time_ns()))
    out.mkdir()
    result = {'status': 'HOLD', 'steps': 0, 'task_success': 'UNVERIFIED',
              'speed': speed, 'playback_hz': MODEL_HZ * speed}
    io = policy = monitor = None
    stats = {'relief':0,'startup':[]}
    with (out / 'steps.jsonl').open('x', encoding='utf-8', buffering=1) as trace:
        def log(row):
            if row.get('event')=='goal_transmitted':stats['relief']+=int(row.get('grip_relief',False))
            if row.get('event','').startswith('startup_'):stats['startup'].append(row)
            trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')

        try:
            task = 'Open the gripper' if gripper_test else base.TASK
            monitor = Monitor(out.name, task, base.JOINTS, correction_kinds=kinds)
            if not gripper_test:
                print(f'재생 속도 {speed:g}배, 초당 모델 액션 {MODEL_HZ*speed:g}개', flush=True)
                monitor.status('warming', force=True)
                policy = make_policy(context)
            monitor.status('connecting', force=True)
            io = device_class()(context, log, out, gripper_only=gripper_test)
            io.connect()
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
                                              monitor, allow_corrections=web_corrections)
                request = result.pop('operator_request')
                if request is not None:
                    if result['hold']['status'] == 'HOLD_NOT_CONFIRMED':
                        raise base.Stop('기존 정책 목표를 정지하지 못해 교정하지 않았습니다')
                    result.update(status='HOLD', reason='운영자 교정으로 정책 실행 종료')
                    result['corrections'], result['hold'] = corrections.session(
                        io, monitor, request, forward, min(deadline, time.monotonic() + 30), log)
            result['torque_off'] = 'TORQUE_RETAINED'
            if (supported_home and (result['status'] == 'RUN_COMPLETE' or result['reason'] == 'Ctrl+C')
                    and result['hold']['status'] == 'CURRENT_POSITION_GOAL_TRANSMITTED'):
                try:
                    sample = io.start_snapshot()
                    moved = live_start.return_home(io, sample, context['home']['state'], 1.0)
                    result['park'] = 'RETURNED_HOME' if moved else 'ALREADY_AT_HOME'
                    result['torque_off'] = io.release_if_supported_home(True)
                except Exception as exc:
                    result.update(park='RELEASE_NOT_COMPLETED', release_error=str(exc))
        except (KeyboardInterrupt, Exception) as exc:
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
            result['startup_auto_processed']=stats['startup']
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
                result = run_case(case, 14, DEFAULT_SPEED, [0.] * 6, {'sample': True}, policy)
                self.assertTrue(result['passed'], result)

    def test_unsafe_goal_readback_and_unverified_route_prevent_all_writes(self):
        from startup_simulation import SimulatedArm

        io = SimulatedArm([0.] * 6, {}, lambda _: None, 'all_off')
        io.target['elbow_flex'] = 3087
        with self.assertRaisesRegex(base.Stop, 'elbow_flex'):
            live_start.prepare(io, io.home, lambda _: None)
        self.assertEqual(io.start_writes, [])
        io = SimulatedArm([0.] * 6, {}, lambda _: None, 'outside_home')
        io.home_path_verified = False
        with self.assertRaisesRegex(base.Stop, '이동 경로 미검증'):
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
        self.assertEqual(chunk_alignment(31, 1.4, rate, 45, moving=True), (13,45))
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
