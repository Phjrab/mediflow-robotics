"""현재 위치 목표 설정 → OFF 축만 토크ON → 저속 홈 이동."""
import math
import time
import json
from collections import deque
from statistics import median
from config_runtime import load
from direct_live import JOINTS, Stop, feedback_gate
from start_position_rules import HomeConvergence, home_targets, validate_position as check_position

EARLY_LIFT_SETTLE_S = 1.25
LATE_LIFT_PROGRESS_S = 0.75
LATE_LIFT_EXTRA_RESIDUAL_TICKS = 3
LATE_LIFT_PROBE_S = 0.25

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
    c=load();targets=home_targets(c)
    velocity=c['startup']['max_deg_s']*globals().get('ACTIVE_SPEED',c['speed'])
    trim=c['startup']['home_trim']
    checks=c['position_checks']
    settle_timeout=checks['settle_timeout_s']
    final_attempt=checks['home_attempts']
    command=dict(targets)
    io.home_command_trim=dict.fromkeys(trim['axes'],0)
    def settle(attempt,saved=0.):
        window=HomeConvergence(targets,c)
        start=clock()
        normal_deadline=start+settle_timeout
        until=normal_deadline
        reserved_deadline=normal_deadline+saved
        probed=False
        extended=False
        history=deque()
        while True:
            sample=io.start_snapshot()
            ensure_health(sample)
            if any(value != 1 for value in sample['torque'].values()):
                raise Stop('홈 정착 중 토크 상태 변경: '+repr(sample['torque']))
            validate_position(sample,io.arm_bounds)
            now=clock()
            active=sample['active_targets']['shoulder_lift']
            acknowledged=attempt==0 or active==command['shoulder_lift']
            if not acknowledged or (history and (now<=history[-1][0] or now-history[-1][0]>checks['max_sample_gap_s'])):
                history.clear()
            lift=sample['present_raw']['shoulder_lift']
            if acknowledged:
                history.append((now,lift))
            # Keep enough history to distinguish late progress from a plateau.
            while len(history)>1 and history[1][0]<=now-LATE_LIFT_PROGRESS_S:
                history.popleft()
            result=window.observe(sample['present_raw'],now)
            if not acknowledged:
                window=HomeConvergence(targets,c)
                result.update(ready=False,stable_seconds=0.)
            lift_home=targets['shoulder_lift']
            # Include one sample just before the stability boundary.
            stable_rows=list(history)
            while len(stable_rows)>1 and stable_rows[1][0]<=now-trim['stable_s']:
                stable_rows.pop(0)
            values=[value for _,value in stable_rows]
            span=max(values)-min(values) if values else None
            residual=lift-lift_home
            stable=bool(values and now-stable_rows[0][0]+1e-9>=trim['stable_s']
                        and span<=trim['stable_span_ticks'])
            result['trim_evidence']={'shoulder_lift':dict(
                stable=stable,span_ticks=span,residual_ticks=round(median(values))-lift_home if values else residual,
                active_target_ticks=active,active_target_confirmed=acknowledged)}
            result['settle_elapsed_s']=now-start
            lift_only=all(row['converged'] for name,row in result['joints'].items() if name!='shoulder_lift')
            tolerance_ticks=checks['home_tolerance_ticks']
            if result['ready']:
                log(dict(event='startup_home_converged',attempt=attempt,**result))
                return sample,result
            if 0<attempt<final_attempt and now<until and now-start>=EARLY_LIFT_SETTLE_S and (
                    lift_only and stable and span==0 and abs(residual)>tolerance_ticks):
                result['settle_early']='stable_lift_only'
                return sample,result
            if now>=until:
                if not acknowledged:
                    raise Stop(f'홈 어깨 들기 목표 응답 불일치: 전송 {command["shoulder_lift"]}틱, 활성 {active}틱')
                gained=abs(history[0][1]-lift_home)-abs(residual) if history else 0
                near_home=abs(residual)<=tolerance_ticks+LATE_LIFT_EXTRA_RESIDUAL_TICKS
                can_wait=(attempt==final_attempt and not extended and saved>0 and lift_only
                          and near_home and now<reserved_deadline)
                if can_wait and (result['home_converged'] or gained>=1):
                    extended=True
                    until=reserved_deadline
                    log(dict(event='startup_home_settle_extension',attempt=attempt,
                        active_target_ticks=active,residual_ticks=residual,recent_progress_ticks=gained,
                        read_only_remaining_s=until-now))
                elif can_wait and not probed and now<normal_deadline+LATE_LIFT_PROBE_S:
                    probed=True
                    until=min(reserved_deadline,normal_deadline+LATE_LIFT_PROBE_S)
                else:
                    return sample,result
            pause(.05)
    saved=0.
    if check_position(sample['present_raw'])['home_converged']:
        sample,result=settle(0)
        if result['ready']:return False
    for attempt in range(1,final_attempt+1):
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
        sample,result=settle(attempt,saved)
        if result.get('settle_early'):
            saved+=settle_timeout-result['settle_elapsed_s']
        log(dict(event='startup_home_result',attempt=attempt,command_ticks=dict(command),
            home_command_trim=dict(io.home_command_trim),settle_saved_s=saved,**result))
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
