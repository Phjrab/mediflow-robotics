"""현재 위치 목표 설정 → OFF 축만 토크ON → 저속 홈 이동."""
import math
import time
from config_runtime import load
from direct_live import JOINTS, Stop, feedback_gate

def exact_sync_packet(address, width, values):
    parameters=bytes((address,width))+b''.join(bytes((mid,))+value.to_bytes(width,'little') for mid,value in values.items())
    body=bytes((254,len(parameters)+2,131))+parameters
    return b'\xff\xff'+body+bytes((255-sum(body)%256,))

def home_delta(sample, home):
    return {j:v-h for j,v,h in zip(JOINTS,sample['state'],home)}

def ensure_health(sample):
    feedback_gate(sample)
    if any(sample['modes'][j]!=0 or sample['torque'][j] not in (0,1) for j in JOINTS):
        raise Stop('위치 모드 또는 토크 응답 형식 오류')

def validate_position(sample, limits):
    for j,v in sample.get('present_raw',{}).items():
        if type(v) is not int or not 0<=v<=4095:raise Stop('현재 위치 응답 형식 오류: '+j)

def wait_for_devices(inspect,report,*,camera_only=False,clock=time.monotonic,pause=time.sleep):
    end=clock()+20
    while True:
        s=inspect();rows={k:v for k,v in s['devices'].items() if not camera_only or k!='serial'}
        if s['lelab']=='inactive' and s['astra']=='active' and all(v['owners_ok'] for v in rows.values()):return s
        if clock()>=end:raise Stop('포트 열기 실패: 점유 해제 미확인')
        report('장치 점유 해제 대기');pause(.25)

def arm(io,sample,log):
    ensure_health(sample)
    original=dict(sample['present_raw'])
    # 초기 목표는 소프트 제한 밖이어도 현재 위치와 정확히 같아야 한다.
    pending=[j for j in JOINTS if sample['torque'][j]==0]
    for attempt in range(1,4):
        if not pending:return sample
        current=io.start_snapshot();ensure_health(current)
        pending=[j for j in pending if current['torque'][j]==0]
        targets={j:int(current['present_raw'][j]) for j in pending}
        if not targets:return current
        io.write_start('Goal_Position',targets)
        before=io.start_snapshot();ensure_health(before)
        for j in pending:
            if abs(before['active_targets'][j]-before['present_raw'][j])>3:
                raise Stop(f'토크 자동ON 실패: {j} 목표/현재 차이3틱 초과')
        off={j:1 for j in pending if before['torque'][j]==0}
        if off:io.write_start('Torque_Enable',off)
        sample=io.start_snapshot();ensure_health(sample)
        errors={j:abs(sample['active_targets'][j]-sample['present_raw'][j]) for j in pending}
        log(dict(event='startup_torque_readback',attempt=attempt,torque=sample['torque'],goal_error_ticks=errors))
        if any(v>3 for v in errors.values()):raise Stop('토크 자동ON 후 현재/목표3틱 초과')
        pending=[j for j in pending if sample['torque'][j]==0]
    if pending:raise Stop('토크 자동ON 실패: '+','.join(pending))
    return sample

def return_home(io,sample,home,tolerance,*,clock=None,pause=None):
    clock=clock or io.clock;pause=pause or io.pause
    start=list(sample['state']);delta=[h-v for h,v in zip(home,start)]
    if max(map(abs,delta))<=tolerance:return False
    c=load();velocity=c['startup']['max_deg_s']*globals().get('ACTIVE_SPEED',c['speed'])
    duration=max(.3,1.875*max(map(abs,delta))/velocity)
    steps=math.ceil(duration*60);begin=clock()
    for n in range(1,steps+1):
        pause(max(0,begin+n*duration/steps-clock()));io.checked()
        t=n/steps;f=t*t*t*(10+t*(-15+6*t))
        io.send(io.plan([v+d*f for v,d in zip(start,delta)])['wire_raw'])
    until=clock()+3
    while clock()<until:
        if max(map(abs,home_delta(io.checked(),home).values()))<=tolerance:return True
        pause(.05)
    raise Stop('홈 이동 후 위치 확인 실패')

def prepare(io,home,log):
    c=load();sample=io.start_snapshot();ensure_health(sample);validate_position(sample,io.arm_bounds)
    policy_limits=dict(io.arm_bounds)
    # 복구 구간은 현재 위치부터 안전 범위까지의 단조 경로만 추가 허용한다.
    expanded={j:(min(lo,sample['present_raw'][j]),max(hi,sample['present_raw'][j])) for j,(lo,hi) in policy_limits.items()}
    io.arm_bounds=expanded;io.raw_bounds=expanded
    def update_bounds():
        a=io.normalize({j:r[0] for j,r in io.raw_bounds.items()});b=io.normalize({j:r[1] for j,r in io.raw_bounds.items()})
        io.bounds=[tuple(sorted(pair)) for pair in zip(a,b)]
    update_bounds()
    adjusted=[j for j,(lo,hi) in policy_limits.items() if not lo<=sample['present_raw'][j]<=hi]
    if adjusted:log(dict(event='startup_range_recovery',joints=adjusted,current=sample['present_raw']))
    sample=arm(io,sample,log)
    # config home은 검증된 정책 범위로 제한한다.
    target=io.normalize(io.plan(home)['wire_raw'])
    return_home(io,sample,target,.05 if adjusted else c['home']['tolerance_deg'])
    io.raw_bounds=policy_limits;io.arm_bounds=policy_limits;update_bounds()
    sample=io.start_snapshot();ensure_health(sample)
    if any(v!=1 for v in sample['torque'].values()):raise Stop('토크 자동ON 최종 확인 실패')
    summary=dict(torque=sample['torque'],modes=sample['modes'],temperature_c=sample['temperatures'],
                 home_delta=home_delta(sample,target),camera=dict.fromkeys(io.camera_map,'READY'),auto_recovered=adjusted)
    log(dict(event='startup_ready',**summary));return summary
