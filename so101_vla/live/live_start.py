"""현재 위치 목표 설정 → OFF 축만 토크ON → 저속 홈 이동."""
import math
import time
import json
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

def return_home(io, sample, home, tolerance=None, *, clock=None, pause=None, log=None):
    clock = clock or io.clock
    pause = pause or io.pause
    log = log or getattr(io, 'log', lambda _: None)
    config = load()
    targets = home_targets(config)
    checks = config['position_checks']
    velocity = config['startup']['max_deg_s'] * globals().get('ACTIVE_SPEED', config['speed'])

    def settle(attempt):
        window = HomeConvergence(targets, config)
        deadline = clock() + checks['settle_timeout_s']
        while True:
            feedback = io.start_snapshot()
            ensure_health(feedback)
            if any(v != 1 for v in feedback['torque'].values()):
                raise Stop('홈 정착 중 토크 상태 변경: ' + repr(feedback['torque']))
            validate_position(feedback, io.arm_bounds)
            active = feedback['active_targets']
            now = clock()
            result = window.observe(feedback['present_raw'], now)
            # An already aligned arm does not need a new command. After sending
            # one, wait for motor readback before counting a stable window.
            mismatch = {j: active.get(j) for j in JOINTS if active.get(j) != targets[j]} if attempt else {}
            if mismatch:
                window = HomeConvergence(targets, config)
                result.update(ready=False, stable_seconds=0.)
            result['active_target_mismatch_ticks'] = mismatch
            if result['ready']:
                log(dict(event='startup_home_converged', attempt=attempt, **result))
                return feedback, result
            if now >= deadline:
                return feedback, result
            pause(.05)

    if check_position(sample['present_raw'])['home_converged']:
        sample, result = settle(0)
        if result['ready']:
            return False

    start = list(sample['state'])
    delta = [goal-value for goal, value in zip(io.normalize(targets), start)]
    duration = max(.3, 1.875 * max(map(abs, delta)) / velocity)
    steps = math.ceil(duration*60)
    for attempt in range(1, checks['home_attempts']+1):
        validate_position(sample, io.arm_bounds)
        log(dict(event='startup_home_attempt', attempt=attempt, start_ticks=sample['present_raw'],
                 target_ticks=targets, command_ticks=targets))
        if attempt == 1:
            begin = clock()
            for n in range(1, steps+1):
                pause(max(0, begin+n*duration/steps-clock()))
                validate_position(io.checked(), io.arm_bounds)
                t = n/steps
                weight = t*t*t*(10+t*(-15+6*t))
                raw = targets if n==steps else io.plan([v+d*weight for v,d in zip(start,delta)])['wire_raw']
                io.send(raw)
        else:
            # The only retry target is the original home. Never shift it to
            # overcome stalled motion or replay the approach trajectory.
            validate_position(io.checked(), io.arm_bounds)
            io.send(targets)
        sample, result = settle(attempt)
        log(dict(event='startup_home_result', attempt=attempt, command_ticks=targets, **result))
        if result['ready']:
            return True
    raise Stop('홈 이동 후 위치 확인 실패: '+json.dumps(dict(attempts=attempt, **result), ensure_ascii=False))


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
                 camera=dict.fromkeys(io.camera_map,'READY'),auto_recovered=['home'] if moved else [])
    log(dict(event='startup_ready',**summary));return summary
