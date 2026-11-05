"""동일 레지스터의 6축 RAW 읽기를 묶는다. 모터 쓰기 API 없음."""
import math

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")

def group(bus,name):
    values=bus.sync_read(name,normalize=False,num_retry=0)
    if set(values)!=set(JOINTS):raise ValueError("HOLD: 상태 키 누락/불일치")
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values()):
        raise ValueError("HOLD: 상태 NaN/Inf/형식 오류")
    return values

def torque_modes(bus):
    torque=group(bus,"Torque_Enable")
    if any(type(v) is not int or v!=0 for v in torque.values()):raise ValueError("HOLD: 토크ON/미확인; 추가 serial 읽기 중단")
    modes=group(bus,"Operating_Mode")
    if any(type(v) is not int or v!=0 for v in modes.values()):raise ValueError("HOLD: 위치모드0 아님")
    return torque,modes

def health(bus,load_limit=200.):
    if type(load_limit) not in (int,float) or not math.isfinite(load_limit) or not 0<load_limit<=200:
        raise ValueError("HOLD: 부하 상한 완화/미확인")
    temps=group(bus,"Present_Temperature")
    if any(not 0<=v<45 for v in temps.values()):raise ValueError("HOLD: 45도 이상/미확인; 추가 serial 읽기 중단")
    loads=group(bus,"Present_Load")
    if any(abs(v)>=load_limit for v in loads.values()):raise ValueError("HOLD: 부하 임계 초과")
    status=group(bus,"Status")
    if any(type(v) is not int or v!=0 for v in status.values()):raise ValueError("HOLD: 비정상 Status")
    return temps
