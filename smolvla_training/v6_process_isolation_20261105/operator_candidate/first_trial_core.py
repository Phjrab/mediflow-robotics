"""첫 동작 시험 제어기. 하드웨어 접근은 주입된 단일 adapter만 사용한다."""
from dataclasses import dataclass
import math
import threading
import time
from collections import deque
from types import SimpleNamespace

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")
CAMERAS=("end_effector","ceiling_vertical","ceiling_oblique")

class Hold(RuntimeError):pass

def vector(value):
    if len(value)!=6 or any(type(v) not in (float,int) or not math.isfinite(v) for v in value):raise Hold("6축 유한값 필요")
    if not 0<=value[5]<=100:raise Hold("그리퍼0~100 범위 이탈")
    return list(value)

@dataclass(frozen=True)
class Limits:
    fps:float=30.
    cap:float=.5
    speed:float=20.
    start_temperature:float=50.
    stop_temperature:float=55.
    first_jump:float=10.
    load:float=200.
    max_seconds:float=5.
    max_steps:int=10
    frame_age:float=.25
    camera_skew:float=.1
    def validate(self):
        if self.fps!=30 or not 0<self.cap<=.5 or not 0<self.speed<=20 or self.cap*self.fps>self.speed:raise Hold("주기/이동 제한 오류")
        if not 0<self.start_temperature<=50 or not 0<self.stop_temperature<=55 or self.start_temperature>=self.stop_temperature:raise Hold("온도 제한 오류")
        if not 0<self.first_jump<=10 or not 0<self.load<=200 or not 0<self.max_seconds<=5:raise Hold("첫 시험 상한 완화 거부")
        if type(self.max_steps) is not int or not 1<=self.max_steps<=10:raise Hold("첫 시험 step1~10 필요")
        if not 0<self.frame_age<=.25 or not 0<self.camera_skew<=.1:raise Hold("프레임 신선도 상한 오류")
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in self.__dict__.values()):raise Hold("프로파일 형식 오류")

def sensor_gate(snapshot,limits,*,starting=False,expected_torque=1,require_state=True):
    for name in ("torque","modes","temperatures","loads","statuses"):
        if set(snapshot.get(name,{}))!=set(JOINTS):raise Hold("센서 키 누락: "+name)
    if any(type(v) is not int or v!=expected_torque for v in snapshot["torque"].values()):raise Hold("토크 상태 오류; 추가 명령 금지")
    if any(type(v) is not int or v!=0 for v in snapshot["modes"].values()):raise Hold("위치모드0 아님")
    cap=limits.start_temperature if starting else limits.stop_temperature
    if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<cap for v in snapshot["temperatures"].values()):raise Hold("온도 상한/미확인")
    if any(type(v) not in (float,int) or not math.isfinite(v) or abs(v)>=limits.load for v in snapshot["loads"].values()):raise Hold("과부하/미확인")
    if any(type(v) is not int or v!=0 for v in snapshot["statuses"].values()):raise Hold("비정상 Status")
    return vector(snapshot["state"]) if require_state else None

def home_gate(state,home):
    if home.get("joint_names")!=list(JOINTS) or type(home.get("tolerance_deg")) not in (float,int) or not 0<home["tolerance_deg"]<=5:raise Hold("home 근거 미확인")
    target=vector(home["state"])
    if any(abs(state[i]-target[i])>home["tolerance_deg"] for i in range(5)):raise Hold("home 범위 이탈")

def bounds_gate(values,bounds,*,source="unspecified"):
    values=vector(values)
    if len(bounds)!=6:raise Hold("관절 한계 근거 없음")
    for i,(value,pair) in enumerate(zip(values,bounds)):
        if len(pair)!=2 or any(type(v) not in (float,int) or not math.isfinite(v) for v in pair) or pair[0]>=pair[1] or not pair[0]<=value<=pair[1]:
            error=Hold("관절 한계/형식 이탈")
            error.observed=dict(source=source,joint=JOINTS[i],value=value,bounds=pair)
            raise error

def frame_gate(frames,now,limits,previous=None):
    if set(frames)!=set(CAMERAS):raise Hold("카메라 키 누락")
    stamps=[]
    for role,row in frames.items():
        stamp=row.get("host_received")
        if type(stamp) not in (float,int) or not math.isfinite(stamp) or not 0<=now-stamp<=limits.frame_age:raise Hold("stale/미확인 프레임")
        if row.get("shape")!=[480,640,3] or row.get("dtype")!="uint8" or not row.get("sha256"):raise Hold("RGB 프레임 형식 오류")
        if previous and previous[role]==row["sha256"]:raise Hold("반복 프레임 후보")
        stamps.append(stamp)
    if max(stamps)-min(stamps)>limits.camera_skew:raise Hold("카메라 수신 시각 차이 초과")
    return {role:row["sha256"] for role,row in frames.items()}

def target_gate(action,measured,previous,bounds,limits,dt,*,quantum=None):
    action=vector(action);measured=vector(measured);previous=vector(previous)
    bounds_gate(measured,bounds,source="measured_state")
    bounds_gate(previous,bounds,source="previous_target")
    if any(abs(action[i]-measured[i])>limits.first_jump or abs(action[i]-previous[i])>limits.first_jump for i in range(6)):raise Hold("큰 목표 점프")
    if type(dt) not in (float,int) or not math.isfinite(dt) or dt<=0:raise Hold("명령 간격 미확인")
    result=[]
    supplied_quantum=quantum is not None
    quantum=[0.]*6 if quantum is None else vector(quantum)
    if any((q<=0 if supplied_quantum else q<0) or q>=(limits.cap if i<5 else 2.) for i,q in enumerate(quantum)):raise Hold("정수 변환 여유 미확인")
    for i in range(6):
        cap=(limits.cap if i<5 else 2.)-quantum[i]
        speed_delta=(limits.speed*dt if i<5 else 2.)-2*quantum[i]
        if speed_delta<0:raise Hold("정수 변환 이후 속도 여유 부족")
        low=max(measured[i]-cap,previous[i]-speed_delta,bounds[i][0]+quantum[i])
        high=min(measured[i]+cap,previous[i]+speed_delta,bounds[i][1]-quantum[i])
        if low>high:raise Hold("현재/이전 목표 제한 교집합 없음")
        result.append(min(high,max(low,action[i])))
    return vector(result)

@dataclass(frozen=True)
class FirstMotionPlan:
    physical_motion_authorized:bool
    stages:tuple=("first_vla_motion",)
    label:str="첫 동작 시험; 자동 약통 투입 성공/G6 아님"

class FirstTrialAdapter:
    """기존 RobotController.run에서 호출하는 단일 단계 adapter."""
    def __init__(self,hardware,evidence,home,bounds,*,limits=Limits(),waiter=None,clock=time.monotonic,sleep=time.sleep):
        limits.validate();self.io=hardware;self.physical=hardware.physical;self.evidence=evidence;self.home=home;self.bounds=bounds
        self.limits=limits;self.waiter=waiter;self.clock=clock;self.sleep=sleep;self.stop=threading.Event()
        self.events=[];self.sent=[];self.connected=False;self.prepared=False;self.previous_hash=None;self.monitor_count=0
    def connect(self):
        self.io.connect();self.connected=True;self.events.append("connect")
    def read_state(self):
        s=self.io.snapshot();sensor_gate(s,self.limits,starting=True)
        if type(s.get("moving")) not in (bool,int) or s["moving"] not in (False,0):raise Hold("시작 시 움직임/Moving 미확인")
        return SimpleNamespace(connected=True,moving=False,torque_enabled=True,temperatures_c=tuple(s["temperatures"].values()))
    def prepare(self):
        if not all(self.evidence.get(k) is True for k in ("model_verified","units_verified","mapping_verified","operator_live_confirmed","arm_receipt_verified")):
            raise Hold("실행 근거/현장 LIVE 확인 미충족")
        if self.physical and self.evidence.get("kind")!="OPERATOR_ARMED":raise Hold("실기 근거에 fixture 사용 금지")
        s=self.io.snapshot();state=sensor_gate(s,self.limits,starting=True);home_gate(state,self.home);bounds_gate(state,self.bounds)
        self.prepared=True;self.events.append("prepared_no_write")
    def monitor(self):
        if self.stop.is_set():raise Hold("정지 요청")
        self.io.check_records();sensor_gate(self.io.health_snapshot(),self.limits,require_state=False)
        self.monitor_count+=1
    def await_job(self,fn,deadline,timeout):
        if self.waiter is None:raise Hold("감시 대기 구현 없음")
        start=self.clock();limit=min(deadline,start+timeout)
        value=self.waiter(fn,self.monitor,deadline=limit,timeout=timeout)
        self.monitor()
        if self.clock()>limit:raise Hold("최종 상태 확인 포함 deadline 초과")
        return value
    def execute_stage(self,stage):
        if stage!="first_vla_motion" or not self.prepared:raise Hold("미준비/알 수 없는 단계")
        limits=self.limits;start=self.clock();deadline=start+limits.max_seconds
        frames=self.await_job(self.io.capture_frames,deadline,1.)
        self.previous_hash=frame_gate(frames,self.clock(),limits)
        state=sensor_gate(self.io.snapshot(),limits,starting=True)
        if not getattr(self,"skip_home_recheck",False):home_gate(state,self.home)
        captured=min(row["host_received"] for row in frames.values())
        inference_started=self.clock()
        actions=self.await_job(lambda:self.io.predict(frames,state),deadline,50/limits.fps)
        self.policy_chunk=dict(raw_actions=actions,observation_state=list(state),bounds=self.bounds,
                               inference_seconds=self.clock()-inference_started,fps=limits.fps)
        if self.clock()-captured>50/limits.fps:raise Hold("정책 입력 나이/추론 지연 초과")
        if len(actions)!=50:raise Hold("행동50개 필요")
        for row in actions:vector(row)
        previous=state[:];last_sent=None;clip_window=min(20,limits.max_steps);clips=deque(maxlen=clip_window)
        compensated=getattr(self,"compensated",False);self.step_timings=[]
        quantum=self.io.motor_quantum() if getattr(self,"quantized",False) else None
        if quantum is not None:self.policy_chunk["motor_quantum"]=quantum
        for step,action in enumerate(actions[:limits.max_steps]):
            timing=dict(step=step);self.step_timings.append(timing);work_started=self.clock()
            if self.clock()>=deadline or self.stop.is_set():raise Hold("시간 상한/정지 요청")
            if last_sent is not None and not compensated:self.sleep(max(0,last_sent+1/limits.fps-self.clock()))
            mark=self.clock()
            if step%5==0:
                frames=self.await_job(self.io.capture_frames,deadline,1.)
                self.previous_hash=frame_gate(frames,self.clock(),limits,self.previous_hash)
            timing["camera_seconds"]=self.clock()-mark;mark=self.clock()
            s=self.io.snapshot();measured=sensor_gate(s,limits)
            sampled_at=self.clock();timing["snapshot_seconds"]=sampled_at-mark
            if step==0 and not getattr(self,"skip_home_recheck",False):home_gate(measured,self.home)
            mark=self.clock()
            if compensated:self.io.check_records()
            timing["records_seconds"]=self.clock()-mark
            due=None if last_sent is None else last_sent+1/limits.fps
            dt=1/limits.fps if last_sent is None else (max(self.clock(),due) if compensated else self.clock())-last_sent
            self.last_target_context=dict(step=step,raw_action=action,measured_state=measured,
                                          previous_target=previous,bounds=self.bounds,dt=dt)
            try:target=target_gate(action,measured,previous,self.bounds,limits,dt,quantum=quantum)
            except Hold as error:
                error.observed=dict(target_context=self.last_target_context,gate=getattr(error,"observed",None))
                raise
            clips.append(any(abs(a-b)>1e-6 for a,b in zip(action,target)))
            if len(clips)==clip_window and sum(clips)/clip_window>.5:
                if getattr(self,"clip_warning_only",False) and (limits.max_steps<=10 or (getattr(limits,"work_segment",False) and limits.max_steps<=50)):
                    self.events.append("clip_ratio_warning");timing["clip_ratio_warning"]=sum(clips)/clip_window
                else:raise Hold("정책-제어 clip 비율 초과")
            if self.clock()>=deadline or self.stop.is_set():raise Hold("전송 직전 시간/정지")
            if not compensated:self.io.check_records()
            mark=self.clock()
            if compensated and due is not None:self.sleep(max(0,due-self.clock()))
            timing["wait_seconds"]=self.clock()-mark
            if self.clock()>=deadline or self.stop.is_set():raise Hold("전송 직전 시간/정지")
            timing["state_age_seconds"]=self.clock()-sampled_at
            if compensated and timing["state_age_seconds"]>1/limits.fps+1e-9:raise Hold("전송 전 관측 지연 초과")
            sent_at=self.clock();self.io.send_target(target)
            timing.update(send_seconds=self.clock()-sent_at,step_seconds=self.clock()-work_started,sent_at=sent_at)
            self.sent.append(dict(step=step,raw_action=action,target=target,state=measured,clip=clips[-1],sent_at=sent_at))
            previous=target;last_sent=sent_at
        self.events.append("max_steps_completed")
    def stop_new_commands(self):
        # RobotController finally에서 반드시 성공해야 close가 이어진다. 모터 쓰기 없음.
        self.stop.set();self.events.append("stop_new_commands_no_write")
    def close(self):
        self.stop.set()
        try:self.io.close()
        finally:self.connected=False;self.events.append("close_no_torque_change")
