"""November SO101 VLA operator trial. Never runs hardware in --mock."""
import argparse
import contextlib
import json
import math
import os
import signal
import time
import unittest
from pathlib import Path
from communication_retry import RetryableWireError

JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
TASK = 'Put medicine bottle A into the blue basket'
PERIOD = 1 / 30
STEPS = math.ceil(63551 / 137 + 5 * 30)
WALL_SECONDS = 75
from config_runtime import load as load_config
ELBOW_MAX = load_config()['limits']['elbow_flex'][1]  # Operator reported physical endpoint (96.57 degrees).


class Stop(Exception):
    pass


def vector(values, size):
    if len(values) != size:
        raise Stop('모델 액션 형식 불일치')
    result = [float(x) for x in values]
    if not all(math.isfinite(x) for x in result):
        raise Stop('모델 액션에 NaN/Inf')
    return result


def chunk(values):
    if len(values) != 50:
        raise Stop('모델 액션 묶음 길이가 50이 아님')
    return [vector(row, len(JOINTS)) for row in values]


def clip_to_bounds(values, bounds):
    return [min(high, max(low, val)) for val, (low, high) in zip(values, bounds, strict=True)]


def feedback_gate(sample):
    protection = sample.get('temperature_protection')
    if protection is not None and (set(protection) != set(JOINTS) or any(
            type(row.get('hold')) is not bool or row.get('source') != 'fresh_individual_20ms'
            for row in protection.values())):
        raise Stop('온도 창 판정 형식 오류')
    bad = {j: {'status': sample['statuses'][j], 'temperature_c': sample['temperatures'][j]}
           for j in JOINTS if sample['statuses'][j] or (
               protection[j]['hold'] if protection is not None
               else sample['temperatures'][j] >= load_config()['temperature']['stop_c'])}
    if protection is not None:
        for joint, row in bad.items():
            row['temperature_protection'] = protection[joint]
    if bad:
        raise Stop('모터 상태코드/온도 중단: ' + json.dumps(bad, ensure_ascii=False))


def parse_reply(packet, comm, mid, width):
    raw = bytes(packet or ())
    observed=dict(expected_id=mid,expected_bytes=width,communication=comm,raw_hex=raw.hex(),
                  received_id=raw[2] if len(raw)>2 else None,
                  checksum_valid=len(raw)>=6 and sum(raw[2:])%256==255)
    if comm in (-6,-7):
        raise RetryableWireError(f'모터 응답 검증 실패: id={mid}, 통신 코드={comm}',observed)
    if (comm != 0 or len(raw) != width + 6 or raw[:2] != b'\xff\xff' or raw[2] != mid
            or raw[3] != width + 2 or sum(raw[2:]) % 256 != 255):
        received = raw[2] if len(raw) >= 3 and raw[:2] == b'\xff\xff' else None
        raise Stop(f'모터 응답 검증 실패: id={mid}, 통신 코드={comm}, 받은 id={received}, 응답 {len(raw)}B')
    if raw[4]:
        raise Stop(f'모터 패킷 상태코드: id={mid}, status={raw[4]}')
    return raw[5:-1]


def read_sync_replies(handler, port, ids, address, width, policy=None):
    replies = {}
    failure = None
    retryable=[]
    operation=f'read:{address}:{width}'
    for joint, mid in ids.items():
        if policy is None:packet,comm=handler.rxPacket(port)
        else:
            with policy.packet_deadline(port,policy.clock()+.02):packet,comm=handler.rxPacket(port)
        # Consume the rest of this request before a new HOLD read. The SDK's
        # clearPort() flushes output and leaves unread status packets in RX.
        if policy is not None:policy.receive(mid,operation,packet,comm)
        try:
            replies[joint] = parse_reply(packet, comm, mid, width)
            if policy is not None:policy.good(mid,operation)
        except RetryableWireError as exc:
            if policy is None:failure=failure or exc
            else:
                policy.bad([mid],operation,exc);retryable.append((joint,mid))
        except Stop as exc:
            failure=failure or exc
    if failure is not None:
        raise Stop(f'{failure}, 읽기 주소={address}, 요청 폭={width}B') from failure
    if policy is not None:
        for joint,mid in retryable:
            port.is_using=False
            body=bytes((255,255,mid,4,2,address,width))
            request=body+bytes(((~sum(body[2:]))&255,))
            def once(mid=mid,request=request):
                packet,comm,_=handler.txRxPacket(port,list(request))
                policy.receive(mid,operation,packet,comm)
                return parse_reply(packet,comm,mid,width)
            replies[joint]=policy.perform([mid],operation,once,port=port,initial_failures=1)
    return replies


def wire_goals(packet):
    data = bytes(packet)
    return {data[i]: int.from_bytes(data[i+1:i+3], 'little') for i in range(7, len(data)-1, 3)}


def try_hold(io):
    try:
        result=io.hold()
        if isinstance(result,dict):return result
        return {'status': 'CURRENT_POSITION_GOAL_TRANSMITTED'}
    except Exception as exc:
        return {'status': 'HOLD_NOT_CONFIRMED', 'reason': str(exc)}


class Cadence:
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep, self.next = clock, sleep, None

    def wait(self, deadline):
        if self.next is not None:
            self.sleep(max(0, min(deadline, self.next) - self.clock()))
        if self.clock() >= deadline:
            raise Stop('실행 시간 상한')

    def sent(self):
        # Never send a burst to make up for lateness.
        self.next = self.clock() + PERIOD


def run(io, predict, log, *, steps=STEPS, seconds=WALL_SECONDS, cadence=None):
    cadence = cadence or Cadence()
    start = cadence.clock()
    deadline = start + seconds
    baseline = None
    final = None
    last = None
    count = 0
    mins, maxs = None, None
    command_min, command_max = None, None
    hold = {'status': 'NOT_NEEDED'}
    reason = None
    try:
        baseline = io.checked()
        mins, maxs = list(baseline['state']), list(baseline['state'])
        while count < steps:
            t0 = cadence.clock()
            actions = chunk(predict(deadline))
            inference = cadence.clock() - t0
            log({'event': 'chunk', 'before_step': count, 'prediction_and_capture_s': inference})
            for target in actions[:steps-count]:
                final = io.checked()
                if cadence.clock() >= deadline:
                    raise Stop('실행 시간 상한')
                plan = io.plan(target)
                record = {'step': count+1, 'time_s': cadence.clock()-start, 'model': target,
                          'feedback': final, **plan}
                log({'event': 'goal_attempt', **record})
                cadence.wait(deadline)
                io.send(plan['wire_raw'])
                sent_at = cadence.clock()
                cadence.sent()
                count += 1
                log({'event': 'goal_transmitted', 'step': count,
                     'gap_s': None if last is None else sent_at-last})
                last = sent_at
                sent = plan['wire_normalized']
                command_min = list(sent) if command_min is None else [min(a,b) for a,b in zip(command_min,sent)]
                command_max = list(sent) if command_max is None else [max(a,b) for a,b in zip(command_max,sent)]
                mins = [min(a,b) for a,b in zip(mins,final['state'])]
                maxs = [max(a,b) for a,b in zip(maxs,final['state'])]
        cadence.wait(deadline)
        final = io.checked()
        log({'event': 'final_feedback', **final})
    except (KeyboardInterrupt, Exception) as exc:
        reason = 'Ctrl+C' if isinstance(exc, KeyboardInterrupt) else str(exc)
        if io.goal_attempted:
            hold = try_hold(io)
        log({'event': 'stop', 'reason': reason, 'hold': hold})
    motion = {}
    if final is not None and baseline is not None:
        for i, name in enumerate(JOINTS):
            value = final['state'][i]
            actual = max(maxs[i], value) - min(mins[i], value)
            sent_span = None if command_min is None else command_max[i] - command_min[i]
            motion[name] = {'sent_span': sent_span, 'observed_span': actual,
                            'observed_delta': value-baseline['state'][i],
                            'observed_to_sent_span': actual / sent_span if sent_span else None,
                            'unit': 'percent_0_100' if i == 5 else 'degree'}
    return {'status': 'HOLD' if reason is not None else 'RUN_COMPLETE', 'reason': reason,
            'hold': hold, 'steps': count, 'elapsed_s': cadence.clock()-start,
            'task_success': 'UNVERIFIED', 'tracking': motion}


def device_class():
    import fcntl
    import termios
    from trial_runtime import DeviceIO, check_context
    from first_trial_core import CAMERAS
    import device_preflight
    from individual_reply_guard import StrictIndividualReplies
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from live_safety_runtime import PacketPermission
    from probe_current_state import PORT

    class DirectIO(DeviceIO):
        def __init__(self, ctx, policy):
            super().__init__(ctx, policy=policy)
            self.goal_attempted = False
            self.wire_fault = False
            self.expected = None
            self.preflight = None

        def connect(self):
            self.preflight = device_preflight.require()
            check_context(self.context)
            self.robot = SO101Follower(SO101FollowerConfig(port=PORT, id='so-101',
                calibration_dir=self.context['calibration'].parent, cameras={}, use_degrees=True,
                max_relative_target=None, disable_torque_on_disconnect=False))
            self.bus = self.robot.bus
            self.ids = {name: self.bus.motors[name].id for name in JOINTS}
            if list(self.ids.values()) != list(range(1, 7)):
                raise Stop('6축 모터 ID 계약 불일치')
            for motor in self.bus.motors.values():
                table = self.bus.model_ctrl_table[motor.model]
                for name, addr in {'Goal_Position':(42,2), 'Present_Position':(56,2),
                                   'Present_Load':(60,2), 'Present_Temperature':(63,1),
                                   'Status':(65,1), 'Torque_Enable':(40,1)}.items():
                    if tuple(table.get(name, ())) != addr:
                        raise Stop('설치 SDK 레지스터 계약 불일치: '+name)
            self.raw_bounds = {j: (self.bus.calibration[j].range_min,
                                   min(self.bus.calibration[j].range_max, ELBOW_MAX) if j == 'elbow_flex'
                                   else self.bus.calibration[j].range_max) for j in JOINTS}
            if any(not 0 <= low < high <= 4095 for low, high in self.raw_bounds.values()):
                raise Stop('보정 가동범위 형식 오류')
            a = self.normalize({j: self.raw_bounds[j][0] for j in JOINTS})
            b = self.normalize({j: self.raw_bounds[j][1] for j in JOINTS})
            self.bounds = [tuple(sorted((lo,hi))) for lo,hi in zip(a,b)]
            io = self

            class Permission(PacketPermission):
                READS = {**PacketPermission.READS, 33:8, 56:15}

                def inspect(self, port, packet):
                    kind = super().inspect(port, packet)
                    if kind == 'goal' and wire_goals(packet) != io.expected:
                        self.blocked_packets += 1
                        raise Stop('SDK 패킷 정수 목표 불일치')
                    return kind

            self.permission = Permission(self.bus.port_handler, allow_goal=True).install()
            self.bus.connect(handshake=False)
            fcntl.ioctl(self.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
            if hasattr(self,'configure_transport'):self.configure_transport()
            self.checked()
            with StrictIndividualReplies(self.bus.packet_handler,retry=getattr(self,'communication',None)):
                if not self.robot.is_calibrated:
                    raise Stop('모터와 저장된 보정 범위 불일치; 보정 쓰기 없음')
            self.check_start_mode()
            self.connect_cameras()
            if set(self.camera_map) != set(CAMERAS):
                raise Stop('카메라 입력 불일치')

        def check_start_mode(self):
            modes = self.read_block(33, 8)
            # Goal writes can energize an OFF motor on this particular setup.
            if any(modes[j][0] != 0 or modes[j][7] != 1 for j in JOINTS):
                raise Stop('위치 모드 및 6축 토크 ON 필요; 자동 토크 변경 없음: '+str(
                    {j: {'mode': modes[j][0], 'torque': modes[j][7]} for j in JOINTS}))

        def read_block(self, address, width):
            bus = self.bus
            read_ids=getattr(self,'read_ids',self.ids)
            mids = list(read_ids.values())
            policy=getattr(self,'communication',None)
            def once():
                code=bus.packet_handler.syncReadTx(bus.port_handler,address,width,mids,len(mids))
                if code in (-6,-7):raise RetryableWireError('센서 일괄 읽기 송신 오류',dict(communication=code,address=address,width=width))
                if code!=0:raise Stop('센서 일괄 읽기 송신 오류')
                return read_sync_replies(bus.packet_handler,bus.port_handler,read_ids,address,width,policy)
            try:
                if policy is None:return once()
                return policy.perform(mids,f'read:{address}:{width}',once,port=bus.port_handler,packet_timeout_s=None)
            finally:
                bus.port_handler.is_using = False

        def normalize(self, raw):
            values = self.bus._normalize({self.ids[j]:raw[j] for j in JOINTS})
            return vector([values[self.ids[j]] for j in JOINTS],len(JOINTS))

        def feedback(self):
            block = self.read_block(56, 15)
            def read(register, index, j):
                mid = self.ids[j]
                raw = int.from_bytes(block[j][index:index+2], 'little')
                return self.bus._decode_sign(register,{mid:raw})[mid]
            ticks = {j:read('Present_Position',0,j) for j in JOINTS}
            return {'state': self.normalize(ticks), 'present_raw': ticks,
                    'loads': {j:read('Present_Load',4,j) for j in JOINTS},
                    'temperatures': {j:block[j][7] for j in JOINTS},
                    'statuses': {j:block[j][9] for j in JOINTS}}

        def checked(self):
            sample = self.feedback()
            feedback_gate(sample)
            return sample

        health_snapshot = checked

        def plan(self, target):
            bounded = clip_to_bounds(target, self.bounds)
            encoded = self.bus._unnormalize(dict(zip(self.ids.values(), bounded)))
            raw = {j: min(high,max(low,int(encoded[self.ids[j]])))
                   for j,(low,high) in self.raw_bounds.items()}
            return {'bounded':bounded, 'wire_raw':raw, 'wire_normalized':self.normalize(raw),
                    'clipped_joints':[j for j,a,b in zip(JOINTS,target,bounded) if a != b]}

        def send(self, raw):
            if set(raw) != set(JOINTS) or any(type(raw[j]) is not int or not low <= raw[j] <= high
                                              for j,(low,high) in self.raw_bounds.items()):
                raise Stop('SDK 전송 전 정수 목표 범위 오류')
            self.expected = {self.ids[j]: raw[j] for j in JOINTS}
            self.goal_attempted = True
            try:
                with self.permission.goal_once():
                    self.bus.sync_write('Goal_Position', raw, normalize=False, num_retry=0)
            except BaseException:
                self.wire_fault = True
                raise
            finally:
                self.expected = None

        def hold(self):
            if self.wire_fault:
                raise Stop('직전 송신 결과가 불확실하여 추가 쓰기 금지')
            self.send(self.feedback()['present_raw'])

    return DirectIO


def live():
    from bounded_wait import wait
    from trial_runtime import PolicyProvider, static_context
    ctx = static_context()
    out = Path.home() / ('vla_direct_blue_' + str(time.time_ns()))
    out.mkdir(exist_ok=False)
    io, policy = None, None
    result = {'status':'HOLD','steps':0,'task_success':'UNVERIFIED'}
    with (out / 'steps.jsonl').open('x',encoding='utf-8',buffering=1) as f:
        def log(row):
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        try:
            policy = PolicyProvider(ctx,TASK)
            io = device_class()(ctx,policy)
            io.connect()
            sample = io.checked()
            home_delta = {j:v-h for j,v,h in zip(JOINTS,sample['state'],ctx['home']['state'])}
            warnings = {j:d for j,d in home_delta.items() if abs(d)>0.5}
            log({'event':'start','home_delta':home_delta,'home_warning':warnings,
                 'raw_bounds':io.raw_bounds,'task':TASK,'duration_action_s':STEPS/30,
                 'device_preflight':io.preflight,'prior_stop_inventory':ctx['stop_inventory']})
            if warnings:
                print('시작 자세 차이 경고: '+json.dumps(warnings),flush=True)
            def predict(deadline):
                frames = wait(io.capture_frames,io.checked,deadline=deadline,timeout=3)
                state = io.checked()['state']
                t0 = time.monotonic()
                actions = wait(lambda:policy.predict(frames,state),io.checked,deadline=deadline,timeout=8)
                log({'event':'inference','seconds':time.monotonic()-t0})
                return actions
            result = run(io,predict,log)
        except (KeyboardInterrupt, Exception) as exc:
            result['reason'] = 'Ctrl+C' if isinstance(exc,KeyboardInterrupt) else str(exc)
            if io is not None and io.goal_attempted and 'hold' not in result:
                result['hold'] = try_hold(io)
        finally:
            with contextlib.ExitStack() as stack:
                prior = signal.signal(signal.SIGINT,signal.SIG_IGN)
                stack.callback(signal.signal,signal.SIGINT,prior)
                if io is not None:
                    result['goal_packet_attempts'] = getattr(io.permission,'goal_packets',0)
                    try:
                        io.close()
                    except BaseException as exc:
                        result['cleanup_exception'] = str(exc)
                    result['cleanup_errors'] = io.cleanup_errors
                    port = getattr(getattr(getattr(io,'bus',None),'port_handler',None),'ser',None)
                    result['port_closed'] = not bool(getattr(port,'is_open',False))
                elif policy is not None:
                    policy.close()
            result.update(output=str(out),torque_register_writes=0,configuration_register_writes=0)
            with (out/'summary.json').open('x',encoding='utf-8') as report:
                json.dump(result,report,ensure_ascii=False,indent=2,allow_nan=False)
            print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status']=='RUN_COMPLETE' and result['port_closed'] and not result.get('cleanup_errors') and not result.get('cleanup_exception') else 1


class MockTests(unittest.TestCase):
    def test_corrupt_sync_read_discards_tail_before_fresh_hold_read(self):
        from types import SimpleNamespace

        def reply(mid, code=0):
            return bytes([255, 255, mid, 3, 0, 8, 244 - mid]), code

        ids = dict(zip(JOINTS, range(1, 7), strict=True))
        packets = iter([reply(mid, -7 if mid == 5 else 0) for mid in ids.values()]
                       + [reply(mid) for mid in ids.values()])
        handler = SimpleNamespace(rxPacket=lambda _: next(packets))
        with self.assertRaisesRegex(Stop, r'id=5, 통신 코드=-7.*읽기 주소=56'):
            read_sync_replies(handler, None, ids, 56, 1)
        self.assertEqual(read_sync_replies(handler, None, ids, 56, 1), dict.fromkeys(JOINTS, b'\x08'))

    def test_direct_no_tracking_or_slew_clip(self):
        self.assertEqual(clip_to_bounds([80,-60,120,0,70,103],[(-90,90)]*5+[(0,100)]),
                         [80,-60,90,0,70,100])
        self.assertEqual(STEPS,614)

    def test_mock_run_and_stop(self):
        for fault in (None,'temperature','status','interrupt','bad_packet','invalid_action'):
            with self.subTest(fault=fault):
                class IO:
                    goal_attempted = False
                    sent = []
                    holds = 0
                    def checked(self):
                        if self.sent and fault=='interrupt': raise KeyboardInterrupt()
                        if self.sent and fault=='bad_packet': raise Stop('CRC')
                        sample = dict(state=[0]*6, temperatures=dict.fromkeys(JOINTS,55 if self.sent and fault=='temperature' else 34),
                                      statuses=dict.fromkeys(JOINTS,1 if self.sent and fault=='status' else 0))
                        feedback_gate(sample)
                        return sample
                    def plan(self,t): return {'wire_raw':t,'wire_normalized':t}
                    def send(self,t): self.goal_attempted=True; self.sent.append(t)
                    def hold(self):
                        if fault=='bad_packet':raise Stop('CRC')
                        self.holds+=1
                io = IO()
                ticks = [0.0]
                def sleep(dt):ticks[0]+=dt
                rows = []
                actions = [[33,-38,41,30,-17,67]]*50 if fault!='invalid_action' else [[float('nan')]*6]*50
                r=run(io,lambda _:actions,rows.append,steps=2,cadence=Cadence(lambda:ticks[0],sleep))
                self.assertEqual(r['steps'],2 if fault is None else (0 if fault=='invalid_action' else 1))
                self.assertEqual(io.holds,int(fault in ('temperature','status','interrupt')))
                if fault is None:
                    self.assertEqual(io.sent,actions[:2])
                    self.assertGreaterEqual(rows[-2]['gap_s'],PERIOD)
                if fault=='bad_packet': self.assertEqual(r['hold']['status'],'HOLD_NOT_CONFIRMED')

    def test_packets_and_no_catchup(self):
        buf=bytes([255,255,1,3,0,8,243])
        self.assertEqual(parse_reply(buf,0,1,1),b'\x08')
        with self.assertRaises(Stop): parse_reply(buf,0,2,1)
        ticks=[0.0]
        def sleep(dt):ticks[0]+=dt
        c=Cadence(lambda:ticks[0],sleep)
        c.sent();ticks[0]=1;c.wait(3);c.sent();c.wait(3)
        self.assertAlmostEqual(ticks[0],1+PERIOD)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--mock',action='store_true')
    mode.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if args.mock:
        unittest.main(argv=['direct_live.py'],verbosity=2)
    if os.environ.get('SO101_DIRECT_OPERATOR')!='1' or not os.isatty(0):
        parser.error('현장 터미널에서 run.sh --live 사용')
    raise SystemExit(live())
