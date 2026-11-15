"""Operator-run recorded trajectory playback. Only position-goal writes permitted."""
import argparse,hashlib,json,math,os,time
from pathlib import Path
import direct_live as base
import live_start
from config_runtime import load
from stream_live import device_class,interpolate
from vla_monitor import Monitor

def require_goal_only_packet(packet):
    r=bytes(packet)
    valid=(len(r)>=6 and r[:2]==b'\xff\xff' and len(r)==r[3]+4 and sum(r[2:])%256==255)
    if not valid or (r[4] not in (2,130) and not (r[4]==131 and r[5:7]==bytes((42,2)))):
        raise base.Stop('녹화 시험 Goal_Position 외 쓰기 차단')

def load_recording(path):
    raw=path.read_bytes();expected=path.with_suffix(path.suffix+'.sha256').read_text().strip()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('녹화 재생 파일 해시 불일치')
    value=json.loads(raw)
    if (value.get('task')!='Put medicine bottle A into the blue basket' or value.get('fps')!=30
            or value.get('joints')!=list(base.JOINTS) or value.get('calibration_sha256')!=load()['calibration_sha256']):
        raise ValueError('녹화 재생 계약 불일치')
    actions=value['actions']
    if not 50<=len(actions)<=3000:raise ValueError('녹화 길이 범위 오류')
    for row in actions:
        if len(row)!=6 or not all(type(v) in (float,int) and math.isfinite(v) for v in row) or not 0<=row[5]<=100:
            raise ValueError('유효하지 않은 녹화 액션')
    return value

def replay(io,actions,log,speed=.33,monitor=None):
    """Shared by physical and fake I/O. No model, compensation or torque calls."""
    if not 0<speed<=1:raise ValueError('재생 속도 오류')
    clock,pause=io.clock,io.pause;duration=(len(actions)-1)/30/speed
    begin=clock();next_tick=begin;last_send=None;count=0;rate=60;reason=None;hold={'status':'NOT_NEEDED'}
    observed=[[] for _ in base.JOINTS];commands=[[] for _ in base.JOINTS];periods=[];clipped={j:0 for j in base.JOINTS}
    lows=io.normalize({j:v[0] for j,v in io.raw_bounds.items()});highs=io.normalize({j:v[1] for j,v in io.raw_bounds.items()})
    try:
        initial=io.start_snapshot();live_start.ensure_health(initial)
        if any(v!=1 for v in initial['torque'].values()):raise base.Stop('녹화 재생은 6축 토크ON 필요; 토크 쓰기는 하지 않음')
        io.motion_ready=True
        # Only an existing low-speed Goal_Position home transfer; never arm.
        live_start.return_home(io,initial,load()['home']['state'],load()['home']['tolerance_deg'])
        begin=clock();next_tick=begin
        while True:
            pause(max(0.,next_tick-clock()));tick=clock();phase=min(len(actions)-1,(tick-begin)*30*speed)
            if monitor is not None and monitor.hold_requested():raise base.Stop('운영자가 관제 화면에서 정지 요청')
            sample=io.checked();lower=math.floor(phase);upper=min(lower+1,len(actions)-1)
            action=interpolate(actions[lower],actions[upper],phase-lower)
            plan=io.plan(action);io.send(plan['wire_raw']);sent=clock();count+=1
            for i,j in enumerate(base.JOINTS):
                observed[i].append(sample['state'][i]);commands[i].append(plan['wire_normalized'][i])
                if j in plan.get('clipped_joints',()) or not min(lows[i],highs[i])<=action[i]<=max(lows[i],highs[i]):clipped[j]+=1
            if last_send is not None:periods.append(sent-last_send)
            last_send=sent
            log(dict(event='recorded_goal_transmitted',step=count,time_s=sent-begin,source_phase=phase,
                     source_action=action,feedback=sample,**plan))
            if monitor is not None:
                monitor.status('moving',step=count,sample=sample,model=action,sent=plan['wire_normalized'])
                if count%10==1:
                    frames=io.camera_frames()
                    if frames is not None:monitor.capture(frames,sample['state'])
            if phase>=len(actions)-1:break
            if rate==60 and sent-tick>.016:
                rate=50;log(dict(event='cadence_fallback',target_hz=rate,work_s=sent-tick))
            next_tick=max(tick+1/rate,sent)
    except (KeyboardInterrupt,Exception) as e:reason='Ctrl+C' if isinstance(e,KeyboardInterrupt) else str(e)
    finally:
        if io.goal_attempted:hold=base.try_hold(io)
    tracking={j:dict(sent_span=max(commands[i])-min(commands[i]),observed_span=max(observed[i])-min(observed[i]))
        for i,j in enumerate(base.JOINTS) if commands[i]}
    for row in tracking.values():row['span_ratio']=row['observed_span']/row['sent_span'] if row['sent_span'] else None
    return dict(status='STOPPED' if reason=='Ctrl+C' else 'HOLD' if reason else 'RUN_COMPLETE',reason=reason,
        replay=True,model_used=False,steps=count,speed=speed,source_frames=len(actions),planned_duration_s=duration,
        elapsed_s=clock()-begin,hold=hold,tracking=tracking,range_clip_counts=clipped,
        transmission_hz=(len(periods)/sum(periods)) if periods else None,final_rate_target=rate,task_success='OPERATOR_REVIEW_REQUIRED')

def run_operator(path,speed=.33):
    from trial_runtime import static_context
    source=load_recording(path);context=static_context();root=Path(__file__).parents[1]
    out=root/'runs'/('REPLAY_BLUE_'+time.strftime('%Y%m%d_%H%M%S'));out.mkdir(exist_ok=False)
    print(f'모델 없이 녹화 blue 액션 {len(source["actions"])}프레임을 {speed}배로 재생합니다. 가동범위 클리핑은 유지합니다.',flush=True)
    print('큰 동작 경로와 약통/바구니 위치를 확인하고 팔 주변을 비우세요. Ctrl+C로 현재 위치 유지 후 종료합니다.',flush=True)
    io=monitor=None;result=dict(status='HOLD',steps=0,model_used=False)
    with (out/'steps.jsonl').open('x',encoding='utf-8',buffering=1) as f:
        def log(row):f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        try:
            class GoalOnlyIO(device_class()):
                def write_start(self,*args,**kwargs):raise base.Stop('녹화 시험 토크/시작 레지스터 쓰기 금지')
                def release_if_supported_home(self,*args,**kwargs):raise base.Stop('녹화 시험 토크 쓰기 금지')
                def configure_transport(self):
                    super().configure_transport();original=self.bus.port_handler.writePort
                    def position_only(packet):
                        require_goal_only_packet(packet)
                        return original(packet)
                    self.bus.port_handler.writePort=position_only
            monitor=Monitor(out.name,'녹화 재생(모델 없음): '+source['task'],base.JOINTS)
            monitor.status('connecting',force=True);io=GoalOnlyIO(context,log,out);io.connect()
            log(dict(event='recording_source',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     source=source.get('source_repo_id'),episode=source.get('source_episode_index'),speed=speed))
            result=replay(io,source['actions'],log,speed,monitor)
        except (KeyboardInterrupt,Exception) as e:
            result['reason']='Ctrl+C' if isinstance(e,KeyboardInterrupt) else str(e)
            if io is not None and io.goal_attempted:result['hold']=base.try_hold(io)
        finally:
            if io is not None:
                result.update(communication=io.communication.summary(),peak_temperatures_c=io.peak_temperatures,
                    thermal_protection=io.thermal_window.summary(),camera=io.cameras_stats.summary())
                try:io.close()
                except Exception as e:result['cleanup_exception']=str(e)
                result.update(port_closed=not bool(getattr(getattr(io.bus.port_handler,'ser',None),'is_open',False)),cleanup_errors=io.cleanup_errors)
            result.update(output=str(out),torque_register_writes=0,configuration_register_writes=0)
            if monitor is not None:monitor.finish(result['status'],result.get('reason'),result['steps'],result.get('hold'))
            (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status']=='RUN_COMPLETE' and result.get('port_closed') else 1

