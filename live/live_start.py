"""현재 위치 목표 설정 → OFF 축만 토크ON → 저속 홈 이동."""
import math
import time
import json
from collections import deque
from statistics import median
from config_runtime import load
from direct_live import JOINTS, Stop, feedback_gate
from start_position_rules import HomeConvergence, home_targets, validate_position as check_position

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
    try:return check_position(sample.get('present_raw',{}),limits)
    except ValueError as exc:raise Stop(str(exc)) from exc

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
        targets={j:min(io.arm_bounds[j][1],max(io.arm_bounds[j][0],int(current['present_raw'][j]))) for j in pending}
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

def return_home(io,sample,home,tolerance=None,*,clock=None,pause=None,log=None):
    # Legacy degree tolerance callers now use the same configured tick rule.
    clock=clock or io.clock;pause=pause or io.pause
    log=log or getattr(io,'log',lambda _:None)
    c=load();targets=home_targets(c);home=io.normalize(targets)
    velocity=c['startup']['max_deg_s']*globals().get('ACTIVE_SPEED',c['speed'])
    trim=c['startup']['home_trim']
    command=dict(targets)
    io.home_command_trim=dict.fromkeys(trim['axes'],0)
    def settle(sample,attempt):
        window=HomeConvergence(targets,c);until=clock()+c['position_checks']['settle_timeout_s']
        history=deque()
        while True:
            validate_position(sample,io.arm_bounds)
            now=clock()
            if history and (now<=history[-1][0] or now-history[-1][0]>c['position_checks']['max_sample_gap_s']):
                history.clear()
            history.append((now,dict(sample['present_raw'])))
            # Keep one sample just before the half-second boundary.
            while len(history)>1 and history[1][0]<=now-trim['stable_s']:
                history.popleft()
            result=window.observe(sample['present_raw'],now)
            result['trim_evidence']={}
            for j in trim['axes']:
                values=[row[j] for _,row in history]
                span=max(values)-min(values)
                result['trim_evidence'][j]=dict(stable=bool(now-history[0][0]+1e-9>=trim['stable_s']
                    and span<=trim['stable_span_ticks']),span_ticks=span,
                    residual_ticks=round(median(values))-targets[j])
            if result['ready']:
                log(dict(event='startup_home_converged',attempt=attempt,**result));return sample,result
            if clock()>=until:return sample,result
            pause(.05);sample=io.checked()
    if check_position(sample['present_raw'])['home_converged']:
        sample,result=settle(sample,0)
        if result['ready']:return False
    for attempt in range(1,c['position_checks']['home_attempts']+1):
        validate_position(sample,io.arm_bounds)
        # A retry continues the previous command. Restarting at measured position
        # would unwind the correction and jump 14 ticks in the reported failure.
        start=list(sample['state']) if attempt==1 else io.normalize(previous_command)
        if attempt>1:
            for j in trim['axes']:
                evidence=result['trim_evidence'][j]
                error=evidence['residual_ticks']
                if not evidence['stable'] or abs(error)<=c['position_checks']['home_tolerance_ticks']:
                    continue
                offset=io.home_command_trim[j]-max(-trim['max_step_ticks'],min(trim['max_step_ticks'],error))
                offset=max(-trim['max_total_ticks'],min(trim['max_total_ticks'],offset))
                lo,hi=io.arm_bounds[j]
                command[j]=max(lo,min(hi,targets[j]+offset))
                io.home_command_trim[j]=command[j]-targets[j]
                log(dict(event='startup_home_trim',attempt=attempt,joint=j,
                    canonical_target_ticks=targets[j],commanded_target_ticks=command[j],
                    offset_ticks=io.home_command_trim[j],residual_ticks=error,
                    stable_span_ticks=evidence['span_ticks']))
        goal=io.normalize(command);delta=[h-v for h,v in zip(goal,start)]
        duration=max(.3,1.875*max(map(abs,delta))/velocity)
        steps=math.ceil(duration*60);begin=clock()
        log(dict(event='startup_home_attempt',attempt=attempt,start_ticks=sample['present_raw'],
            target_ticks=targets,command_ticks=dict(command),home_command_trim=dict(io.home_command_trim)))
        for n in range(1,steps+1):
            pause(max(0,begin+n*duration/steps-clock()));validate_position(io.checked(),io.arm_bounds)
            t=n/steps;f=t*t*t*(10+t*(-15+6*t))
            # Send the exact final command integer; convergence stays canonical.
            raw=dict(command) if n==steps else io.plan([v+d*f for v,d in zip(start,delta)])['wire_raw']
            io.send(raw)
        previous_command=dict(command)
        sample,result=settle(io.checked(),attempt)
        log(dict(event='startup_home_result',attempt=attempt,command_ticks=dict(command),
            home_command_trim=dict(io.home_command_trim),**result))
        if result['ready']:return True
    raise Stop('홈 이동 후 위치 확인 실패: '+json.dumps(dict(attempts=attempt,**result),ensure_ascii=False))

def prepare(io,home,log):
    c=load();sample=io.start_snapshot();ensure_health(sample);validate_position(sample,io.arm_bounds)
    policy_limits=dict(io.arm_bounds)
    assessment=check_position(sample['present_raw'])
    if assessment['warnings']:log(dict(event='startup_position_warning',**assessment))
    # Observation margins never widen any command bounds. An OFF axis in the
    # three-tick observation margin is seeded at the clipped nearest goal.
    sample=arm(io,sample,log)
    io.raw_bounds=policy_limits;io.arm_bounds=policy_limits
    target=io.normalize(home_targets(c))
    moved=return_home(io,sample,target,log=log)
    sample=io.start_snapshot();ensure_health(sample)
    if any(v!=1 for v in sample['torque'].values()):raise Stop('토크 자동ON 최종 확인 실패')
    final_position=check_position(sample['present_raw'])
    if not final_position['home_converged']:
        raise Stop('홈 수렴 후 새 표본 잔차 이탈: '+json.dumps(final_position,ensure_ascii=False))
    summary=dict(torque=sample['torque'],modes=sample['modes'],temperature_c=sample['temperatures'],
                 home_delta=home_delta(sample,target),position_check=final_position,
                 home_command_trim=dict(io.home_command_trim),
                 camera=dict.fromkeys(io.camera_map,'READY'),auto_recovered=['home'] if moved else [])
    log(dict(event='startup_ready',**summary));return summary
