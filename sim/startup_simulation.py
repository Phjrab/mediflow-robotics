"""The live policy loop with an in-memory arm, for bench tests and Jetson dry-run."""
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import direct_live as base
import live_start
from stream_live import CameraStats, device_class, make_policy, start_policy_session, wait_until_cool
from vla_monitor import Monitor
from thermal_window import ThermalWindow

CAMERAS = ('end_effector', 'ceiling_vertical', 'ceiling_oblique')
from config_runtime import load
CONFIG=load()
HOME_TICKS=tuple(round((v*4095/360+(lo+hi)/2) if i<5 else lo+v*(hi-lo)/100) for i,(v,(lo,hi)) in enumerate(zip(CONFIG['home']['state'],CONFIG['calibration_ranges'].values())))
TICK_SIZE = (360 / 4095,) * 5 + (100 / (CONFIG['calibration_ranges']['gripper'][1]-CONFIG['calibration_ranges']['gripper'][0]),)
SCENARIOS = ('all_off', 'partial_off', 'outside_home', 'cooling', 'busy_port', 'intermittent_camera',
             'slow_inference', 'blocked_gripper', 'ctrl_c', 'overheat','usb_disconnect','elbow_plus_1','elbow_plus_5','elbow_plus_20','wrist_plus10','wrist_minus10')


class VirtualClock:
    def __init__(self):
        self.now = 0.

    def clock(self):
        return self.now

    def pause(self, delay):
        self.now += max(1e-9, delay)


class VirtualExecutor:
    def __init__(self, clock, slow):
        self.clock = clock
        self.slow = slow
        self.calls = 0

    def submit(self, fn, *args):
        self.calls += 1
        try:
            chunk, elapsed = fn(*args)
        except Exception as exc:
            def failed(error=exc):
                raise error
            return SimpleNamespace(done=lambda: True, result=failed)
        elapsed = max(elapsed, 5.8) if self.slow and self.calls <= 2 else elapsed
        ready = self.clock() + elapsed
        return SimpleNamespace(done=lambda: self.clock() >= ready, result=lambda: (chunk, elapsed))

    def shutdown(self, **_):
        pass


class SimulatedArm:
    home_path_verified = True

    def __init__(self, home, frames, log, case, *, real_time=False):
        self.home = home
        self.frames = frames
        self.log = log
        self.case = case
        self.camera_map = {name: name for name in CAMERAS}
        self.cameras_stats = CameraStats(self.camera_map)
        self.gripper_peak = None
        self.peak_temperatures = {}
        self.goal_attempted = False
        self.motor_writes = 0
        self.packet_count = 0
        self.start_writes = []
        self.cooling_samples = 0
        self.attempts = dict.fromkeys(base.JOINTS, 0)
        self.home_motion_speed = 0.
        self.previous_goal_at = None
        self.gripper_blocked = False
        if real_time:
            self.clock, self.pause = time.monotonic, time.sleep
        else:
            clock = VirtualClock()
            self.clock, self.pause = clock.clock, clock.pause
            self.pool_factory = lambda **_: VirtualExecutor(self.clock, case == 'slow_inference')
        self.last_update = self.clock()
        self.last_temperature_read = None
        self.thermal_window = ThermalWindow(base.JOINTS,CONFIG['temperature'])
        self.present = dict(zip(base.JOINTS, HOME_TICKS))
        if case == 'outside_home':
            self.present['wrist_roll'] += round(15 / TICK_SIZE[4])
        if case.startswith('elbow_plus_'):self.present['elbow_flex']=CONFIG['limits']['elbow_flex'][1]+int(case.rsplit('_',1)[1])
        if case.startswith('wrist_'):self.present['wrist_roll']+=round((10 if case=='wrist_plus10' else -10)/TICK_SIZE[4])
        self.target = dict(self.present)
        self.torque = dict.fromkeys(base.JOINTS, 0 if case == 'all_off' or real_time else 1)
        if case == 'partial_off':
            self.torque.update(wrist_roll=0, gripper=0)
        self.arm_bounds = dict.fromkeys(base.JOINTS, (0, 4095))
        self.arm_bounds['elbow_flex'] = (0, CONFIG['limits']['elbow_flex'][1])
        self.arm_bounds['gripper'] = (math.ceil(HOME_TICKS[-1] - home[-1] / TICK_SIZE[-1]),
                                      math.floor(HOME_TICKS[-1] + (100 - home[-1]) / TICK_SIZE[-1]))
        self.arm_bounds={j:tuple(row) for j,row in CONFIG['limits'].items()}
        self.raw_bounds=self.arm_bounds.copy()
        self.policy_active = False

    def connect(self):
        if self.case == 'busy_port':
            since = self.clock()
            def status():
                free = self.clock() - since >= 2
                return {'lelab': 'inactive', 'astra': 'active', 'devices': {'serial': {'owners_ok': free}}}
            live_start.wait_for_devices(status, lambda message: self.log({'event': 'device_wait', 'message': message}),
                                        clock=self.clock, pause=self.pause)
        def report(temps):
            self.cooling_samples += 1
            self.log({'event': 'startup_cooling', 'temperatures': temps})
        wait_until_cool(self.feedback, report, pause=self.pause)

    def normalize(self, raw):
        return [((raw[j]-(lo+hi)/2)*360/4095 if i<5 else (raw[j]-lo)*100/(hi-lo)) for i,(j,(lo,hi)) in enumerate(CONFIG['calibration_ranges'].items())]

    def feedback(self):
        if self.last_temperature_read is not None:
            self.pause(max(0.,self.last_temperature_read+CONFIG['temperature']['sample_interval_s']-self.clock()))
        now = self.clock()
        self.last_temperature_read = now
        follow = 1 - math.exp(-(now - self.last_update) / .065)
        self.last_update = now
        for j in base.JOINTS:
            if self.torque[j] and not (self.gripper_blocked and j == 'gripper'):
                self.present[j] += follow * (self.target[j] - self.present[j])
        raw = {j: round(pos) for j, pos in self.present.items()}
        temps = dict.fromkeys(base.JOINTS, 34)
        if self.case == 'cooling':
            temps['gripper'] = 52 if now < 10 else (47 if now < 20 else 44)
        if self.case == 'overheat' and self.packet_count >= 20:
            temps['gripper'] = 55
        self.gripper_peak = max(self.gripper_peak or 0, temps['gripper'])
        self.peak_temperatures = {j: max(v, self.peak_temperatures.get(j, 0)) for j, v in temps.items()}
        return {'state': self.normalize(raw), 'present_raw': raw, 'temperatures': temps,
                'temperature_protection': {j:self.thermal_window.observe_axis(j,now,temps[j]) for j in base.JOINTS},
                'statuses': dict.fromkeys(base.JOINTS, 0), 'loads': dict.fromkeys(base.JOINTS, 0),
                'active_targets': self.target.copy(), 'modes': dict.fromkeys(base.JOINTS, 0), 'torque': self.torque.copy()}

    def checked(self):
        if self.case == 'ctrl_c' and self.packet_count >= 20:
            raise KeyboardInterrupt
        if self.case=='usb_disconnect' and self.packet_count>=20:raise OSError('팔로워 USB 끊김')
        sample = self.feedback()
        base.feedback_gate(sample)
        return sample

    start_snapshot = checked

    def write_start(self, register, values):
        assert all(self.torque[j] == 0 for j in values), 'already-enabled axis was written during startup'
        self.start_writes.append((register, dict(values)))
        if register == 'Goal_Position':
            self.target.update(values)
            if self.case == 'all_off' and 'shoulder_pan' in values:
                self.torque['shoulder_pan'] = 1  # Observed hardware side effect.
            return
        assert register == 'Torque_Enable' and set(values.values()) == {1}
        for j in values:
            self.attempts[j] += 1
            if not (self.case == 'partial_off' and j == 'gripper' and self.attempts[j] == 1):
                self.torque[j] = 1

    def fresh_frames(self):
        if self.case == 'intermittent_camera' and self.clock() % 1 < .15:
            self.cameras_stats.failures['ceiling_oblique'] += 1
            return None
        for name in CAMERAS:
            self.cameras_stats.frame(name)
        return self.frames

    def plan(self, values):
        raw = {}
        for i,(j,value) in enumerate(zip(base.JOINTS,values)):
            lo,hi=CONFIG['calibration_ranges'][j]
            encoded=round(value*4095/360+(lo+hi)/2) if i<5 else round(lo+value*(hi-lo)/100)
            low,high=self.arm_bounds[j]
            raw[j]=min(high,max(low,encoded))
        if self.policy_active:
            raw['elbow_flex'] = min(raw['elbow_flex'], CONFIG['limits']['elbow_flex'][1])
        return {'wire_raw': raw, 'wire_normalized': self.normalize(raw)}

    def send(self, raw):
        assert all(self.torque.values())
        if not self.policy_active:
            now = self.clock()
            if self.previous_goal_at is not None:
                prev, old = self.previous_goal_at
                speeds = [abs(raw[j] - old[j]) * factor / (now - prev) for j, factor in zip(base.JOINTS, TICK_SIZE)]
                self.home_motion_speed = max(self.home_motion_speed, *speeds)
            self.previous_goal_at = now, raw
        self.target.update(raw)
        self.goal_attempted = True
        self.packet_count += 1

    def hold(self):
        if self.case=='usb_disconnect':raise OSError('USB 끊김: 유지 목표 전달 불가')
        self.target.update(self.feedback()['present_raw'])


def run_case(case, duration, speed, home, frames, policy, *, real_time=False, camera_source=None, monitor=None):
    evidence = {'first_targets': [], 'model_chunks': 0, 'relief_steps': 0, 'max_arm_error_ticks': 0,
                'port_waits': 0, 'physical_motor_writes': 0}
    def log(row):
        event = row['event']
        if event == 'goal_transmitted':
            if len(evidence['first_targets']) < 10:
                evidence['first_targets'].append(row['wire_raw'])
            evidence['relief_steps'] += row['grip_relief']
        if event == 'inference':
            evidence['model_chunks'] += 1
        if event == 'startup_torque_readback':
            evidence['max_arm_error_ticks'] = max(evidence['max_arm_error_ticks'], *row['goal_error_ticks'].values())
        if event == 'startup_ready':
            io.policy_active = True
        if event == 'device_wait':
            evidence['port_waits'] += 1
    io = SimulatedArm(home, frames, log, case, real_time=real_time)
    io.connect()
    if camera_source is not None:
        camera_source.open_cameras(io.checked)
        io.fresh_frames = camera_source.fresh_frames
        io.camera_frames = camera_source.camera_frames
        io.camera_map, io.cameras_stats = camera_source.camera_map, camera_source.cameras_stats
        preview = camera_source.camera_frames()
        if preview is not None and monitor is not None:
            sample = io.checked()
            monitor.capture(preview, sample['state'], preview=True)
            monitor.status('connecting', sample=sample, force=True)
    if case == 'blocked_gripper':
        original_policy = policy
        grip_chunks = 0
        def closing_policy(*args):
            nonlocal grip_chunks
            actions, seconds = original_policy(*args)
            grip_chunks += 1
            # Open normally first, then imitate an object obstructing closure.
            # Recorded SO101 direction: high values open, low values close.
            io.gripper_blocked = grip_chunks > 1
            grip_target = 30. if grip_chunks == 1 else 0.
            return [row[:-1] + [grip_target] for row in actions], seconds
        policy = closing_policy
    result = start_policy_session(io, policy, home, log, duration, speed, monitor)
    expected_stop = {'ctrl_c': ('STOPPED', 'Ctrl+C'), 'overheat': ('HOLD', '모터 상태코드/온도 중단'), 'usb_disconnect':('HOLD','팔로워 USB 끊김')}.get(case)
    stopped = (expected_stop is not None and result['status'] == expected_stop[0]
               and (result['reason'] or '').startswith(expected_stop[1]))
    completed = result['status'] == 'RUN_COMPLETE' and result['elapsed_s'] >= duration - .05
    metrics = dict(evidence, status=result['status'], steps=result['steps'], elapsed_s=result['elapsed_s'], reason=result['reason'],
                   hold=result['hold'], chunk_wait_s=result['chunk_wait_s'], transmission_hz=result['transmission_hz'],
                   camera=result['camera'], home_peak_goal_speed=io.home_motion_speed, cooling_samples=io.cooling_samples)
    specific = {'partial_off': io.attempts['gripper'] == 2 and io.attempts['wrist_roll'] == 1,
                'outside_home': 0 < io.home_motion_speed <= 10,
                'cooling': io.cooling_samples >= 2, 'busy_port': evidence['port_waits'] >= 2,
                'intermittent_camera': result['camera']['ceiling_oblique']['read_failures'] > 0,
                'blocked_gripper': evidence['relief_steps'] > 0}
    passed = ((stopped if expected_stop else completed) and result['steps'] > 0 and evidence['model_chunks'] > 0
              and evidence['max_arm_error_ticks'] <= 3 and specific.get(case, True))
    return dict(case=case, passed=passed, **metrics)


def run_simulations(duration, speed):
    import numpy as np
    from PIL import Image
    from trial_runtime import static_context

    context = static_context()
    preview = Path(__file__).resolve().parents[1] / 'analysis/vla_v3_blue_eval_preview'
    frames = {name: {'pixels': np.array(Image.open(preview / f'training_{name}.png').convert('RGB'))} for name in CAMERAS}
    policy = make_policy(context)
    passed = True
    try:
        for case in SCENARIOS:
            policy.reset()
            result = run_case(case, duration, speed, context['home']['state'], frames, policy)
            passed &= result['passed']
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        policy.close()
    return 0 if passed else 1


def run_dry(duration, speed):
    from trial_runtime import static_context

    context = static_context()
    out = Path(__file__).resolve().parents[1] / 'runs' / f'DRY_{time.time_ns()}'
    out.mkdir()
    camera_source = device_class()(context, lambda row: print(json.dumps(row, ensure_ascii=False), flush=True), out)
    monitor = Monitor(out.name, '카메라 확인 · 모터 모의', base.JOINTS, controls=False)
    monitor.status('warming', force=True)
    result = {'passed': False, 'steps': 0}
    policy = None
    try:
        policy = make_policy(context)
        result = run_case('camera_dry', duration, speed, context['home']['state'], {}, policy, real_time=True,
                          camera_source=camera_source, monitor=monitor)
    finally:
        try:
            camera_source.close()
        finally:
            try:
                if policy is not None:
                    policy.close()
            finally:
                monitor.finish('run_complete' if result['passed'] and not camera_source.cleanup_errors else 'hold',
                               result.get('reason'), result['steps'])
    result['output'] = str(out)
    result['camera_cleanup_errors'] = camera_source.cleanup_errors
    result['passed'] &= not result['camera_cleanup_errors']
    (out / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['passed'] else 1
