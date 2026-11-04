"""무인 읽기 전용 계층. READ 이외의 SDK 패킷을 거부하고 안전 중단을 공유한다."""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parent
JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')
PORT='/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00'
CALIBRATION=Path.home()/'.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json'
CALIBRATION_HASH='2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e'
MODEL_HASH='b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654'
READ_REGISTERS={40:1,63:1,33:1,56:2,9:2,11:2,31:2,60:2,65:1}

class SafetyStop(RuntimeError):pass
class ForbiddenCommand(RuntimeError):pass

def latch_path():return ROOT/'SERIAL_STOP.json'
def check_stop():
    if latch_path().exists():raise SafetyStop('serial 전역 중단 상태; 자동 해제하지 않음')
def stop_serial(reason,observed):
    p=latch_path();p.parent.mkdir(parents=True,exist_ok=True)
    try:
        with p.open('x',encoding='utf-8') as stream:
            json.dump(dict(time=time.time(),reason=reason,observed=observed),stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
    except FileExistsError:pass
    raise SafetyStop(reason)

def validate_home(home):
    state=home.get('state',[])
    if home.get('joint_names')!=list(JOINTS) or len(state)!=6 or home.get('tolerance_deg')!=5:
        raise ValueError('home 관절/차원/5도 허용폭 불일치')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in state):
        raise ValueError('home 값 오류')
    if not 0<=state[5]<=100:raise ValueError('home 그리퍼 범위 오류')
    return home

def home_gate(snapshot,home):
    if any(abs(snapshot['positions'][j]-home['state'][i])>5 for i,j in enumerate(JOINTS[:5])):
        raise SafetyStop('HOME5도 범위 이탈; 추론용 실시간 관측 보류')

class PacketGuard:
    def __init__(self):self.read_packets=0;self.blocked=0;self.phase='construction';self.port=None
    def inspect(self,packet):
        try:data=bytes(packet)
        except (ValueError,TypeError):raise ForbiddenCommand('알 수 없는 패킷 형식')
        valid=(len(data)==8 and data[:2]==b'\xff\xff' and data[3]==4 and
               sum(data[2:])%256==255 and data[2] in range(1,7) and data[4]==2 and
               READ_REGISTERS.get(data[5])==data[6])
        if not valid:
            self.blocked+=1;raise ForbiddenCommand('개별 READ 이외 송신 거부; 레지스터 쓰기 금지')
        self.read_packets+=1
    @contextlib.contextmanager
    def installed(self,cls):
        write,opening,closing=cls.writePort,cls.openPort,cls.closePort
        guard=self
        def tx(port,packet):
            if port is not guard.port:raise ForbiddenCommand('다른 포트 송신 거부')
            guard.inspect(packet);count=write(port,packet)
            if count!=len(packet):raise ConnectionError('READ 요청 송신 길이 불일치')
            return count
        def op(port):
            if guard.phase!='opening' or guard.port is not None:raise ForbiddenCommand('예상하지 않은 포트 열기')
            guard.port=port;return opening(port)
        cls.writePort,cls.openPort,cls.closePort=tx,op,closing
        try:yield
        finally:cls.writePort,cls.openPort,cls.closePort=write,opening,closing

def normalize_position(bus,joint,raw):
    # bus.read(normalize=False)는 이미 부호를 해석한 값이다.
    # read(normalize=True)의 공개 소스와 동일하게 ID 사전에 _normalize를 딱 한 번 적용한다.
    motor_id=bus.motors[joint].id
    value=float(bus._normalize({motor_id:raw})[motor_id])
    if not math.isfinite(value) or (joint=='gripper' and not 0<=value<=100):
        raise SafetyStop('관절 정규화 수치/그리퍼 범위 오류')
    return value

def snapshot(bus,home=None):
    check_stop();torque={};temps={};modes={};raw={};positions={}
    # 안전 상태를 먼저 읽는다. 하나라도1이면 다른 레지스터를 더 읽지 않는다.
    for j in JOINTS:
        value=bus.read('Torque_Enable',j,normalize=False,num_retry=0)
        if value not in (0,1):stop_serial('토크 응답 미확인',{**torque,j:value})
        torque[j]=int(value)
        if value==1:stop_serial('토크ON 관측; 이후 serial 작업 중단',dict(joint=j,torque=torque))
    for j in JOINTS:
        value=float(bus.read('Present_Temperature',j,normalize=False,num_retry=0));temps[j]=value
        if not math.isfinite(value) or value<0 or value>=45:
            stop_serial('온도45도 이상 또는 미확인; 이후 serial 작업 중단',dict(joint=j,temperatures=temps))
    for j in JOINTS:
        modes[j]=int(bus.read('Operating_Mode',j,normalize=False,num_retry=0))
        raw[j]=bus.read('Present_Position',j,normalize=False,num_retry=0)
        positions[j]=normalize_position(bus,j,raw[j])
    result=dict(time=time.time(),torque=torque,temperatures=temps,modes=modes,raw_positions=raw,
                positions=positions,register_writes=0)
    if home:
        result['home_delta']={j:positions[j]-home['state'][i] for i,j in enumerate(JOINTS)}
        result['home_ok']=all(abs(result['home_delta'][j])<=5 for j in JOINTS[:5])
    return result

class ReadSession:
    def __init__(self,bus=None,port_class=None,claim=True):
        self.bus=bus;self.robot=None;self.port_class=port_class;self.claim=claim;self.guard=PacketGuard();self.patch=None
    def __enter__(self):
        check_stop()
        if self.port_class is None:
            import scservo_sdk
            self.port_class=scservo_sdk.PortHandler
        self.patch=self.guard.installed(self.port_class);self.patch.__enter__()
        try:
            if self.bus is None:
                if hashlib.sha256(CALIBRATION.read_bytes()).hexdigest()!=CALIBRATION_HASH:
                    raise SafetyStop('보정 해시 불일치; 장치 미연결')
                from lerobot.robots.so_follower import SO101Follower,SO101FollowerConfig
                self.robot=SO101Follower(SO101FollowerConfig(port=PORT,id='so-101',calibration_dir=CALIBRATION.parent,
                    cameras={},use_degrees=True,disable_torque_on_disconnect=False))
                self.bus=self.robot.bus
            self.guard.phase='opening';self.bus.connect(handshake=False);self.guard.phase='reading'
            if self.claim:
                import fcntl,termios
                fcntl.ioctl(self.bus.port_handler.ser.fileno(),termios.TIOCEXCL)
            return self
        except BaseException:
            self.__exit__(*__import__('sys').exc_info());raise
    def read(self,home=None):return snapshot(self.bus,home)
    def verify_motor_calibration(self):
        for j in JOINTS:
            cal=self.bus.calibration[j]
            for register,expected in (('Min_Position_Limit',cal.range_min),('Max_Position_Limit',cal.range_max),
                                       ('Homing_Offset',cal.homing_offset)):
                if self.bus.read(register,j,normalize=False,num_retry=0)!=expected:
                    raise SafetyStop('모터 보정 레지스터와 파일 불일치')
    def __exit__(self,*args):
        try:
            if self.bus is not None and self.bus.is_connected:
                try:self.bus.disconnect(disable_torque=False)
                finally:
                    if self.bus.is_connected:
                        try:self.bus.port_handler.ser.close()
                        finally:self.bus.port_handler.is_open=False
        finally:
            if self.patch:self.patch.__exit__(*args);self.patch=None

def read_home():return validate_home(json.loads((Path.home()/'home_state_so101.json').read_text()))
def read_once(out=None):
    home=read_home()
    with ReadSession() as session:
        value=session.read(home);session.verify_motor_calibration()
        value['read_packets']=session.guard.read_packets
    value['port_closed']=True
    if out:
        with Path(out).open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps(value,ensure_ascii=False),flush=True)
    return value

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out');a=p.parse_args();read_once(a.out)
