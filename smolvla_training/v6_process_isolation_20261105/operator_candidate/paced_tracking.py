"""속도에 맞춘 경로 보간과 추종 정체 감시. 파일·장치·정책 접근 없음."""
from dataclasses import dataclass
import math
from collections import deque
from first_trial_core import Hold,JOINTS,vector,bounds_gate,target_gate

def fault(code,message,**observed):
    error=Hold(message);error.observed=dict(code=code,**observed);return error

def validate_quantum(quantum,limits):
    values=vector(quantum)
    for i,q in enumerate(values):
        if not 0<q<(limits.cap if i<5 else 2.):raise fault('ENCODER_BUDGET','정수 변환 여유 오류',joint=JOINTS[i])
        budget=(limits.speed/limits.fps if i<5 else 2.)-2*q
        if budget<=0:raise fault('ENCODER_BUDGET','정수 변환 이후 속도 여유 없음',joint=JOINTS[i])
    return values

def plan_actions(state,actions,bounds,limits,quantum,*,max_points=5000):
    limits.validate();state=vector(state);bounds_gate(state,bounds,source='paced_anchor')
    quantum=validate_quantum(quantum,limits)
    if len(actions)!=50:raise fault('POLICY_SHAPE','행동50개 필요')
    if type(max_points) is not int or not 1<=max_points<=5000:raise fault('PLAN_BUDGET','경로 점 수 상한 오류')
    raw=[vector(row) for row in actions];points=[];previous=state[:];previous_raw=state[:];clipped=[]
    budgets=[(limits.speed/limits.fps if i<5 else 2.)-2*q for i,q in enumerate(quantum)]
    for index,row in enumerate(raw):
        jumps=[dict(joint=JOINTS[i],difference=abs(row[i]-previous_raw[i]),threshold=limits.first_jump) for i in range(6) if abs(row[i]-previous_raw[i])>limits.first_jump]
        if jumps:raise fault('POLICY_FIRST_TARGET' if index==0 else 'POLICY_DISCONTINUITY','정책 시작/연속 목표 차이 상한 초과',raw_index=index,joints=jumps)
        projected=[]
        for i,value in enumerate(row):
            low,high=bounds[i][0]+quantum[i],bounds[i][1]-quantum[i]
            if low>high:raise fault('ENCODER_BOUNDS','정수 여유를 포함한 보정 범위 없음',joint=JOINTS[i])
            projected.append(min(high,max(low,value)))
        if any(abs(a-b)>1e-9 for a,b in zip(row,projected)):clipped.append(index)
        count=max(1,math.ceil(max(abs(projected[i]-previous[i])/budgets[i] for i in range(6))))
        if len(points)+count>max_points:raise fault('PLAN_BUDGET','보간 경로 상한 초과',raw_index=index)
        for part in range(1,count+1):
            fraction=part/count
            point=[previous[i]+(projected[i]-previous[i])*fraction for i in range(6)]
            bounds_gate(point,bounds,source='paced_waypoint')
            points.append(dict(raw_index=index,fraction=fraction,waypoint=point,raw_action=row[:]))
        previous=projected;previous_raw=row[:]
    return dict(points=points,raw_actions=raw,raw_count=50,planned_points=len(points),boundary_projected_raw_indices=clipped,
                minimum_execution_seconds=len(points)/limits.fps,original_nominal_seconds=50/limits.fps,
                resampled=True,physical_success='UNVERIFIED')

@dataclass(frozen=True)
class TrackingThresholds:
    window_seconds:float=1.
    required_error:float=1.
    progress_degrees:float=.2
    progress_gripper:float=.2
    def validate(self):
        if any(type(x) not in (float,int) or not math.isfinite(x) or x<=0 for x in self.__dict__.values()):raise Hold('추종 감시 임계값 오류')
        if not .5<=self.window_seconds<=2. or not .5<=self.required_error<=1.5:raise Hold('추종 감시 범위 오류')

class ProgressMonitor:
    def __init__(self,quantum,thresholds=TrackingThresholds()):
        thresholds.validate();self.thresholds=thresholds;self.quantum=vector(quantum);self.rows=deque();self.last_now=None
    def observe(self,now,state,previous):
        state=vector(state);previous=vector(previous)
        if type(now) not in (int,float) or not math.isfinite(now) or self.last_now is not None and now<self.last_now:raise fault('CLOCK','관측 시각 역행/형식 오류')
        if any(q<=0 for q in self.quantum):raise Hold('추종 감시 정수 여유 오류')
        self.last_now=now;self.rows.append((now,state,previous))
        while len(self.rows)>1 and now-self.rows[1][0]>=self.thresholds.window_seconds:self.rows.popleft()
        if now-self.rows[0][0]<self.thresholds.window_seconds:return
        stalled=[]
        for i,name in enumerate(JOINTS):
            errors=[abs(s[i]-goal[i]) for _,s,goal in self.rows]
            values=[s[i] for _,s,_ in self.rows]
            span=max(values)-min(values)
            minimum=max(2*self.quantum[i],self.thresholds.progress_degrees if i<5 else self.thresholds.progress_gripper)
            if min(errors)>=self.thresholds.required_error and span<minimum:
                stalled.append(dict(joint=name,error_min=min(errors),observed_span=span,minimum_progress=minimum,
                    unit='degree' if i<5 else 'percent_0_100'))
        if stalled:raise fault('TRACKING_STALLED','관절 추종 정체; 설정을 바꾸지 않고 중단',window_seconds=now-self.rows[0][0],joints=stalled)

class TrajectoryCursor:
    def __init__(self,plan,limits,bounds,quantum):
        self.plan=plan;self.limits=limits;self.bounds=bounds;self.quantum=validate_quantum(quantum,limits);self.index=0
    @property
    def complete(self):return self.index==len(self.plan['points'])
    def propose(self,measured,previous,dt):
        if self.complete:return None
        point=self.plan['points'][self.index]
        try:target=target_gate(point['waypoint'],measured,previous,self.bounds,self.limits,dt,quantum=self.quantum)
        except Hold as error:
            raise fault('PACED_TARGET_GATE',str(error),planned_index=self.index,raw_index=point['raw_index'],gate=getattr(error,'observed',None)) from error
        return dict(point=point,target=target,planned_index=self.index)
    def acknowledge(self,proposal,wire_target):
        wire_target=vector(wire_target)
        if proposal['planned_index']!=self.index:raise fault('PLAN_SEQUENCE','보간 경로 순서 불일치')
        reached=all(abs(a-b)<=q+1e-8 for a,b,q in zip(proposal['point']['waypoint'],wire_target,self.quantum))
        if reached:self.index+=1
        return reached
