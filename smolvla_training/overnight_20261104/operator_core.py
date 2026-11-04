#!/usr/bin/env python3
"""기존 목표가 현재 위치와 같을 때만 토크ON. Goal_Position 쓰기와 SYNC_READ는 금지한다."""
import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import time
import traceback

CALIBRATION_HASH = "2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e"
V4_HASH = "af72d76b992c237211c576796219694503986609277da9b877913b550df6ec56"
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


class BlockedCommand(RuntimeError):
    pass


class WriteGuard:
    """프로세스 내부 SDK 송신 가드. 다른 프로세스/펌웨어를 제어하지 않는다."""
    def __init__(self, mode, emit):
        if mode not in ('enable', 'disable'):
            raise ValueError('모드 오류')
        self.mode, self.emit = mode, emit
        self.phase = 'initialization'
        self.expected = None
        self.used = set()
        self.port = None
        self.opens = 0
        self.reads = 0
        self.writes = 0
        self.goal_verified = False

    def deny(self, reason):
        stack = [dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                 for f in traceback.extract_stack(limit=10)[:-1]]
        self.emit(dict(event='BLOCKED_COMMAND', phase=self.phase, reason=reason, stack=stack))
        raise BlockedCommand(reason)

    def inspect(self, packet):
        try:
            data = bytes(packet)
        except (ValueError, TypeError):
            self.deny('송신 형식 오류')
        if len(data) < 6 or data[:2] != b'\xff\xff' or len(data) != data[3]+4 or sum(data[2:]) % 256 != 255:
            self.deny('패킷 헤더/길이/체크섬 오류')
        motor, instruction, params = data[2], data[4], data[5:-1]
        if instruction == 2 and motor in range(1,7) and len(params) == 2 and params[1] in (1,2,4):
            self.reads += 1
            return False
        # 위치/목표/상태 모두 개별 READ만 사용한다. SYNC_READ도 송신 전에 차단한다.
        if data != self.expected:
            self.deny('허가 없는 쓰기: instruction=%d address=%s' % (instruction, params[0] if params else None))
        self.expected = None  # 한 번만 허용. 재시도는 새 상태 확인 없이 허용하지 않는다.
        self.writes += 1
        self.emit(dict(event='AUTHORIZED_WRITE', phase=self.phase, packet_hex=data.hex()))
        return True

    @contextlib.contextmanager
    def permission(self, name, values):
        if self.expected is not None or name in self.used or set(values) != set(JOINTS):
            self.deny('중첩/반복 쓰기 또는 관절 키 불일치')
        if name == 'Torque_Enable':
            address, width, phase = 40, 1, 'write_explicit_torque'
            target = 1 if self.mode == 'enable' else 0
            if self.mode == 'enable' and not self.goal_verified:
                self.deny('기존 목표와 현재 위치 대조 완료 전 토크ON 금지')
            if any(type(v) is not int or v != target for v in values.values()):
                self.deny('선택한 모드와 토크 쓰기 불일치')
        else:
            self.deny('허가 대상이 아닌 레지스터')
        params = bytes([address,width]) + b''.join(bytes([i])+values[j].to_bytes(width,'little')
                                                 for i,j in enumerate(JOINTS,1))
        head = bytes([255,255,254,len(params)+2,131]) + params
        self.expected = head + bytes([(~sum(head[2:])) & 255])
        self.phase = phase
        try:
            yield
            if self.expected is not None:
                raise RuntimeError('허가된 패킷이 송신되지 않음; 성공으로 처리하지 않음')
            self.used.add(name)
        finally:
            self.expected = None
            self.phase = 'state_check'

    def confirm_existing_goal(self, current, goal):
        if set(current) != set(JOINTS) or set(goal) != set(JOINTS) or current != goal:
            self.deny('기존 목표와 현재 위치 불일치')
        if any(type(v) is not int or not 0 <= v <= 4095 for v in current.values()):
            self.deny('현재 원시 위치 형식/범위 오류')
        self.goal_verified = True
        self.emit(dict(event='EXISTING_GOAL_VERIFIED', current_raw=current, goal_raw=goal, goal_writes=0))

    @contextlib.contextmanager
    def installed(self, cls):
        original_write, original_open, original_close = cls.writePort, cls.openPort, cls.closePort
        guard = self
        def write(port, packet):
            if port is not guard.port:
                guard.deny('다른 포트 송신 시도')
            is_write = guard.inspect(packet)
            count = original_write(port, packet)
            if count != len(packet):
                raise RuntimeError('serial 송신 길이 불일치; 재시도하지 않음')
            if is_write:
                guard.emit(dict(event='WRITE_TRANSMITTED', phase=guard.phase))
            return count
        def open_port(port):
            if guard.phase != 'open' or guard.opens:
                guard.deny('예상하지 않은 포트 열기')
            guard.opens += 1; guard.port = port
            guard.emit(dict(event='PORT_OPEN', phase=guard.phase))
            return original_open(port)
        def close_port(port):
            guard.emit(dict(event='PORT_CLOSE', phase=guard.phase))
            return original_close(port)
        cls.writePort, cls.openPort, cls.closePort = write, open_port, close_port
        try:
            yield
        finally:
            cls.writePort, cls.openPort, cls.closePort = original_write, original_open, original_close


def guarded_write(bus, guard, name, values):
    with guard.permission(name, values):
        bus.sync_write(name, values, normalize=False, num_retry=0)


def validate_sdk(bus):
    module = importlib.import_module(type(bus.packet_handler).__module__)
    for name, expected in dict(PKT_ID=2, PKT_LENGTH=3, PKT_INSTRUCTION=4, PKT_PARAMETER0=5,
                               INST_READ=2, INST_WRITE=3, INST_SYNC_READ=130, INST_SYNC_WRITE=131).items():
        if getattr(module, name, None) != expected:
            raise RuntimeError('SDK 형식 미확인: '+name)
    if tuple(bus.motors) != JOINTS or [m.id for m in bus.motors.values()] != list(range(1,7)):
        raise RuntimeError('모터 이름/순서/ID 불일치')
    for motor in bus.motors.values():
        for name, expected in dict(Torque_Enable=(40,1), Goal_Position=(42,2), Present_Position=(56,2)).items():
            if tuple(bus.model_ctrl_table[motor.model][name]) != expected:
                raise RuntimeError('레지스터 미확인: '+name)


def close_bus(bus):
    try:
        bus.disconnect(disable_torque=False)
    finally:
        if bus.is_connected:
            try:
                bus.port_handler.ser.close()
            finally:
                bus.port_handler.is_open = False


def read_joints(bus, register, normalize=True):
    return {joint: bus.read(register, joint, normalize=normalize, num_retry=0) for joint in JOINTS}


def diagnostics(bus, api, home=None):
    positions = read_joints(bus, "Present_Position")
    values = api.vector([float(positions[j]) for j in api.JOINTS])
    temperatures = api.read_temperatures(bus)
    # 모든 위치/온도 조회 뒤에 모드, 그리고 토크를 마지막으로 읽는다.
    modes = {j: int(bus.read("Operating_Mode", j, normalize=False, num_retry=0)) for j in api.JOINTS}
    torque = fresh_torque(bus, api)
    targets = None
    if home is not None:
        try:
            if home.get("joint_names") != list(api.JOINTS):
                raise ValueError("진단 home 관절 순서 불일치")
            targets = api.vector(home["state"])
        except (ValueError, KeyError, TypeError, AttributeError):
            print("HOME_DIAGNOSTICS_UNVERIFIED: 유효한 home 기준이 없어 차이는 미확인", flush=True)
    rows = [dict(joint=j, torque_enable=torque[j], position=values[i],
                 home=targets[i] if targets else "UNVERIFIED",
                 home_delta=values[i]-targets[i] if targets else "UNVERIFIED",
                 unit="percent_0_100" if i == 5 else "degree", mode=modes[j], temperature=temperatures[j])
            for i,j in enumerate(api.JOINTS)]
    print("MOTOR_DIAGNOSTICS " + json.dumps(rows, ensure_ascii=False), flush=True)
    return torque, modes, positions


def fresh_torque(bus, api):
    values = {j: int(bus.read("Torque_Enable", j, normalize=False, num_retry=0)) for j in api.JOINTS}
    if any(v not in (0, 1) for v in values.values()):
        raise RuntimeError("토크 응답 이상; 상태 미확인")
    return values


def arm(bus, api, home, guard):
    def state_check(stage):
        torque, modes, positions = diagnostics(bus, api, home)
        guard.emit(dict(event="STATE_CHECK", stage=stage, torque=torque, modes=modes, positions=positions))
        if any(t != 0 for t in torque.values()):
            raise RuntimeError("시작 거부: 토크ON 또는 일부ON; 자동 변경하지 않음")
        if any(m != 0 for m in modes.values()):
            raise RuntimeError("시작 거부: 위치 모드0 아님")
        temperatures = api.read_health(bus, starting=True)
        api.home_gate(api.vector([float(positions[j]) for j in api.JOINTS]), home)
        return temperatures, positions
    temps, before = state_check("before_existing_goal_check")
    raw = read_joints(bus, "Present_Position", normalize=False)
    goal = read_joints(bus, "Goal_Position", normalize=False)
    if goal != raw:
        raise RuntimeError("기존 목표와 현재 위치 불일치; 목표 쓰기가 토크ON을 유발한 현장 기록이 있어 자동 수정하지 않음")
    state_check("after_existing_goal_check")
    after = read_joints(bus, "Present_Position")
    if any(abs(after[j] - before[j]) > 0.5 for j in api.JOINTS):
        raise RuntimeError("준비 중 자세0.5단위 초과 변화; 쓰기 없이 중단")
    raw_now = read_joints(bus, "Present_Position", normalize=False)
    goal_now = read_joints(bus, "Goal_Position", normalize=False)
    if raw_now != raw or goal_now != raw_now:
        raise RuntimeError("준비 중 원시 위치 또는 기존 목표 변화; 쓰기 없이 중단")
    torque = fresh_torque(bus, api)
    guard.emit(dict(event="TORQUE_CHECK", stage="before_explicit_on", torque=torque))
    if any(torque.values()):
        raise RuntimeError("활성화 직전 토크ON; 자동 변경하지 않음")
    guard.confirm_existing_goal(raw_now, goal_now)
    guarded_write(bus, guard, "Torque_Enable", dict.fromkeys(api.JOINTS, 1))
    torque, modes = api.read_motor_state(bus)
    guard.emit(dict(event="TORQUE_CHECK", stage="after_explicit_on", torque=torque, modes=modes))
    if any(t != 1 for t in torque.values()) or any(m != 0 for m in modes.values()):
        raise RuntimeError("활성화 일부 실패 또는 모드 불일치; 지지 유지, 자동OFF하지 않음")
    api.read_health(bus)
    current = read_joints(bus, "Present_Position")
    if any(abs(current[j]-before[j]) > 0.5 for j in api.JOINTS):
        raise RuntimeError("활성화 뒤 자세0.5단위 초과 변화; 지지 유지, 추가 쓰기 없음")
    return {"torque":torque, "modes":modes, "temperature_before":temps,
            "current":current, "goal_raw":goal_now, "goal_writes":0}



def release(bus, api, guard, home=None):
    # 일부ON 상태도 허용한다. 전체OFF 요구는 arm()에만 있으며 이 경로에는 없다.
    before, _, _ = diagnostics(bus, api, home)
    guard.emit(dict(event='TORQUE_CHECK', stage='before_explicit_off', torque=before))
    guarded_write(bus, guard, "Torque_Enable", dict.fromkeys(api.JOINTS, 0))
    torque, _, _ = diagnostics(bus, api, home)
    guard.emit(dict(event='TORQUE_CHECK', stage='after_explicit_off', torque=torque))
    if any(value != 0 for value in torque.values()):
        raise RuntimeError("토크OFF 일부 실패: 남은ON=%r. 팔 지지를 유지하고 전원 상태를 현장에서 확인" % [j for j in api.JOINTS if torque[j] != 0])
    time.sleep(0.25)
    torque = fresh_torque(bus, api)
    guard.emit(dict(event='TORQUE_CHECK', stage='after_off_settle', torque=torque))
    if any(torque.values()):
        raise RuntimeError("OFF 확인 뒤 토크 재활성화 감지; 성공으로 보고하지 않음, 지지 유지")
    return torque


def load_v4(path):
    spec = importlib.util.spec_from_file_location("so101_v4", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--confirm-supported", action="store_true")
    p.add_argument("--v4", type=Path, default=Path.home()/"so101_first_action_test_safe_20261104_v4.py")
    p.add_argument("--home", type=Path, default=Path.home()/"home_state_so101.json")
    p.add_argument("--units", type=Path, default=Path.home()/"units_so101.json")
    p.add_argument("--checkpoint", type=Path, default=Path.home()/"smolvla_model/pretrained_model")
    p.add_argument("--port", default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00")
    p.add_argument('--out', type=Path, default=Path.home()/time.strftime('torque_guard_%Y%m%d_%H%M%S'))
    args = p.parse_args()
    if not args.execute:
        print("PLAN_ONLY: 장치 열기/토크 변경 없음; 실제 변경에는 --execute 필요")
        return
    if args.disable and not args.confirm_supported:
        raise SystemExit("거부: 팔/물체를 지지한 뒤 --confirm-supported 필요")
    if hashlib.sha256(args.v4.expanduser().read_bytes()).hexdigest() != V4_HASH:
        raise RuntimeError('v4가 검토본과 다름; 장치 미연결')
    api = load_v4(args.v4.expanduser())
    calibration = Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    if hashlib.sha256(calibration.read_bytes()).hexdigest() != CALIBRATION_HASH:
        raise RuntimeError("현재 보정 해시 불일치")
    home = None
    if args.home.expanduser().is_file():
        try:
            home = json.loads(args.home.expanduser().read_text())
        except (OSError, ValueError):
            if args.enable:
                raise
            print("HOME_DIAGNOSTICS_UNVERIFIED: 토크 해제는 home 파일을 요구하지 않음")
    if args.enable:
        api.verify_units(args.units, args.checkpoint, calibration)
        if api.inspect_checkpoint(args.checkpoint)["checkpoint_kind"] != "SO101_FINETUNED_CANDIDATE":
            raise RuntimeError("base 또는 모델 미확인")
        if home is None:
            raise RuntimeError("home 파일 없음; 활성화 거부")
    # 서비스 점유확인과 팔 지지를 마친 사용자가 직접 실행한다.
    token = "ENABLE" if args.enable else "SUPPORTED"
    print("팔은 지지대로 안정되게 받치고 회전부에서 손을 빼세요. 토크 변경은 물리 작용을 일으킬 수 있습니다.")
    if input("진행하려면 " + token + " 입력: ").strip() != token:
        raise RuntimeError("사용자 확인 불일치; 장치 미연결")
    args.out = args.out.expanduser()
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out/'events.jsonl').open('x', encoding='utf-8') as log:
        def emit(row):
            text = json.dumps(dict(unix=time.time(), **row), ensure_ascii=False)
            print(text, flush=True); log.write(text+'\n'); log.flush()
        guard = WriteGuard('enable' if args.enable else 'disable', emit)
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        import scservo_sdk
        with guard.installed(scservo_sdk.PortHandler):
            robot = SO101Follower(SO101FollowerConfig(port=args.port, id='so-101',
                calibration_dir=calibration.parent, cameras={}, use_degrees=True,
                max_relative_target=2.0, disable_torque_on_disconnect=False))
            bus = robot.bus
            try:
                validate_sdk(bus)
                guard.phase = 'open'
                bus.connect(handshake=False)
                # 보정 확인 전에 배타 잠금. 기존 점유는 별도 preflight에서 거부한다.
                import fcntl, termios
                fcntl.ioctl(bus.port_handler.ser.fileno(), termios.TIOCEXCL)
                guard.phase = 'calibration_check'
                if not robot.is_calibrated:
                    raise RuntimeError('파일과 모터 보정 불일치; 자동 보정하지 않음')
                guard.phase = 'state_check'
                result = arm(bus, api, home, guard) if args.enable else release(bus, api, guard, home)
                emit(dict(event='RESULT', status='TORQUE_ON_HOLD' if args.enable else 'TORQUE_OFF_SUPPORTED',
                          result=result, authorized_writes=guard.writes, reads=guard.reads))
            except BaseException as exc:
                emit(dict(event='STOPPED', kind=type(exc).__name__, reason=str(exc)))
                raise
            finally:
                guard.phase = 'close'
                if bus.is_connected:
                    close_bus(bus)
    print('송신 기록:', args.out/'events.jsonl')


if __name__ == "__main__":
    raise SystemExit("MOCK_CORE_ONLY: 이 사본은 모의 시험용이며 직접 장치를 실행하지 않음")
