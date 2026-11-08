"""서보 설정 읽기 전용 진단. 기본 계획만 출력하며 실기는 운영자 직접 확인 필요."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time
from first_trial_core import JOINTS,Hold
from paced_tracking import fault

MANDATORY={'Torque_Enable':(40,1),'Operating_Mode':(33,1),'Present_Position':(56,2),'Present_Temperature':(63,1)}
OPTIONAL=('P_Coefficient','I_Coefficient','D_Coefficient','CW_Dead_Zone','CCW_Dead_Zone',
          'Torque_Limit','Max_Torque_Limit','Maximum_Torque','Maximum_Acceleration','Acceleration',
          'Goal_Velocity','Present_Velocity','Present_Voltage','Max_Voltage_Limit','Min_Voltage_Limit','Phase',
          'Minimum_Startup_Force','Goal_Position_2','Goal_Position','Present_Load','Present_Current','Return_Delay_Time')
CONFLICTS={'Minimum_Startup_Force':'SDK 주소24 길이2와 공급사 주소24·25 개별 길이1 불일치; 원시 바이트로 별도 기록',
           'Goal_Position_2':'SDK 주소71과 공급사 목표 피드백67 정의 불일치; 주소71 자동 조회 제외'}
RAW_PROFILE={'firmware_major_byte0':(0,1),'firmware_minor_byte1':(1,1),'endianness_byte2':(2,1),
             'model_major_byte3':(3,1),'model_minor_byte4':(4,1),
             'startup_byte24':(24,1),'integral_limit_byte25':(25,1),'target_feedback67':(67,2)}
RAW_PROFILE_SOURCE='https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation'

def register_contract(table):
    for key,pair in MANDATORY.items():
        if tuple(table.get(key,()))!=pair:raise fault('REGISTER_CONTRACT','필수 레지스터 계약 불일치',register=key)
    selected=dict(MANDATORY);unsupported={}
    for name in OPTIONAL:
        if name in CONFLICTS:unsupported[name]=CONFLICTS[name];continue
        row=table.get(name)
        if row is None:unsupported[name]='NOT_FOUND';continue
        if not isinstance(row,(tuple,list)) or len(row)!=2 or any(type(x) is not int for x in row) or not 0<=row[0]<=255 or row[1] not in (1,2):
            unsupported[name]='UNVERIFIED_FORMAT';continue
        if any(addr==row[0] and size!=row[1] for addr,size in selected.values()):raise Hold('레지스터 길이 충돌')
        selected[name]=tuple(row)
    return selected,unsupported

def inspect_bus(bus,selected,*,exclusive=lambda:None,replies=contextlib.nullcontext,seal=lambda:None,raw_read=None):
    result=dict(status='HOLD',live='NOT_RUN',motor_register_writes=0,values={},raw_positions={},
        connected=False,port_closed=False,cleanup_errors=[],actual_units_for_optional='UNVERIFIED',read_history=[],raw_profile_values={})
    def read(register,joint):
        value=bus.read(register,joint,normalize=False,num_retry=0)
        if type(value) is not int:raise fault('READ_FORMAT','원시 레지스터 응답 형식 오류',joint=joint,register=register)
        result['values'].setdefault(register,{})[joint]=value
        result['read_history'].append(dict(register=register,joint=joint,value=value))
        return value
    try:
        bus.connect(handshake=False);result['connected']=True;exclusive()
        with replies():
            for joint in JOINTS:
                value=read('Torque_Enable',joint)
                if value!=0:raise fault('TORQUE_NOT_OFF','토크ON/미확인 감지; 추가 읽기 없이 중단',joint=joint,value=value)
            for joint in JOINTS:
                value=read('Present_Temperature',joint)
                if not 0<=value<45:raise fault('TEMPERATURE','45도 이상/미확인; 추가 읽기 없이 중단',joint=joint,value=value)
            for joint in JOINTS:
                value=read('Operating_Mode',joint)
                if value!=0:raise fault('MODE','위치모드0 아님; 추가 읽기 중단',joint=joint,value=value)
            for joint in JOINTS:result['raw_positions'][joint]=read('Present_Position',joint)
            for register in selected:
                if register in MANDATORY:continue
                for joint in JOINTS:read(register,joint)
            if raw_read is not None:
                for name,(address,size) in RAW_PROFILE.items():
                    result['raw_profile_values'][name]={}
                    for joint in JOINTS:
                        value=raw_read(address,size,joint)
                        if type(value) is not int or not 0<=value<256**size:raise fault('RAW_PROFILE_RESPONSE','원시 프로필 응답 오류',joint=joint,address=address)
                        result['raw_profile_values'][name][joint]=value
            for joint in JOINTS:
                value=read('Torque_Enable',joint)
                if value!=0:raise fault('TORQUE_NOT_OFF','종료 전 토크ON/미확인 감지; 추가 읽기 중단',joint=joint,value=value)
            for joint in JOINTS:
                value=read('Present_Temperature',joint)
                if not 0<=value<45:raise fault('TEMPERATURE','종료 전45도 이상/미확인; 추가 읽기 중단',joint=joint,value=value)
        result['status']='SERVO_CONFIG_READONLY_COMPLETE'
    except BaseException as error:
        result['error']=dict(kind=type(error).__name__,reason=str(error).replace(str(Path.home()),'~'),observed=getattr(error,'observed',None))
    finally:
        try:seal()
        except BaseException as error:result['cleanup_errors'].append(dict(stage='seal',kind=type(error).__name__))
        serial=getattr(getattr(bus,'port_handler',None),'ser',None)
        try:
            if bool(bus.is_connected) or bool(getattr(serial,'is_open',False)):bus.disconnect(disable_torque=False)
        except BaseException as error:result['cleanup_errors'].append(dict(stage='disconnect',kind=type(error).__name__))
        finally:
            if bool(getattr(serial,'is_open',False)):
                try:
                    serial.close()
                    if getattr(serial,'is_open',None) is False:bus.port_handler.is_open=False
                except BaseException as error:result['cleanup_errors'].append(dict(stage='serial_close',kind=type(error).__name__))
        result['port_closed']=not bool(getattr(serial,'is_open',False)) and not bool(bus.is_connected)
        if not result['port_closed'] or result['cleanup_errors']:result['status']='HOLD'
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--operator-present',action='store_true');p.add_argument('--out',type=Path)
    args=p.parse_args()
    if not args.execute:
        print('PLAN_ONLY: 장치·추론·레지스터 쓰기 없음. 토크OFF·45도 미만·현장 직접 READ_CONFIG 확인 후 읽기만 가능합니다.');return 0
    if not args.operator_present or not sys.stdin.isatty():raise Hold('현장 운영자 터미널 확인 필요; 장치 미연결')
    output=(args.out or Path.home()/('servo_readonly_'+str(time.time_ns()))).expanduser()
    if output.exists():raise Hold('기존 출력 경로 덮어쓰기 금지')
    from trial_runtime import dependencies
    dependencies()
    import device_preflight as devices,probe_current_state as probe
    from operator_arm import confirm
    from hold_continuation import CAL_SHA
    calibration=Path.home()/'.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json'
    if hashlib.sha256(calibration.read_bytes()).hexdigest()!=CAL_SHA:raise Hold('보정 파일 해시 불일치; 장치 미연결')
    from lerobot.robots.so_follower import SO101Follower,SO101FollowerConfig
    robot=SO101Follower(SO101FollowerConfig(port=probe.PORT,id='so-101',calibration_dir=calibration.parent,cameras={},use_degrees=True,disable_torque_on_disconnect=False))
    if {name:robot.bus.motors[name].id for name in JOINTS}!={name:i+1 for i,name in enumerate(JOINTS)}:raise Hold('모터 ID 순서 불일치')
    selected,unsupported=register_contract(robot.bus.model_ctrl_table['sts3215'])
    from live_safety_runtime import PacketPermission
    class ReadOnlyPermission(PacketPermission):READS={address:size for address,size in list(selected.values())+list(RAW_PROFILE.values())}
    devices.require()
    confirm('READ_CONFIG','모터 설정 읽기만 수행합니다. 팔을 안정적으로 받치고 DC 전원을 연결한 상태에서 진행하세요. 토크ON 또는45도 이상이면 읽기를 중단하며 토크·목표·설정은 변경하지 않습니다.')
    devices.require();output.mkdir(parents=True,exist_ok=False)
    guard=ReadOnlyPermission(robot.bus.port_handler,allow_goal=False).install()
    from individual_reply_guard import StrictIndividualReplies
    def exclusive():
        import fcntl,termios
        fcntl.ioctl(robot.bus.port_handler.ser.fileno(),termios.TIOCEXCL)
    def raw_read(address,size,joint):
        data,communication,status=robot.bus.packet_handler.readTxRx(robot.bus.port_handler,robot.bus.motors[joint].id,address,size)
        if communication!=0 or status!=0 or len(data)!=size:raise fault('RAW_PROFILE_RESPONSE','원시 프로필 통신 실패; 재시도 없이 중단',joint=joint,address=address,communication=communication,status=status)
        return int.from_bytes(bytes(data),'little')
    result=inspect_bus(robot.bus,selected,exclusive=exclusive,replies=lambda:StrictIndividualReplies(robot.bus.packet_handler),seal=guard.seal,raw_read=raw_read)
    if result['port_closed']:
        try:guard.restore()
        except BaseException as error:
            result['cleanup_errors'].append(dict(stage='guard_restore',kind=type(error).__name__));result['status']='HOLD'
    result.update(register_contract=selected,unsupported_registers=unsupported,read_packets=guard.read_packets,blocked_packets=guard.blocked_packets,
        calibration_sha256=CAL_SHA,raw_profile=RAW_PROFILE,raw_profile_source=RAW_PROFILE_SOURCE,
        source_conflicts=CONFLICTS,output=str(output).replace(str(Path.home()),'~'))
    with (output/'RESULT.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({k:result[k] for k in ('status','motor_register_writes','port_closed','output')},ensure_ascii=False))
    if 'error' in result:print(json.dumps(result['error'],ensure_ascii=False))
    return 0 if result['status']=='SERVO_CONFIG_READONLY_COMPLETE' else 2

if __name__=='__main__':raise SystemExit(main())
