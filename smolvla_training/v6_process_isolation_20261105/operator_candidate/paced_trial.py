"""원래 제어기를 사용하는 모의 전용 보간 실행 후보. 실기 연결은 차단한다."""
from first_trial_core import FirstTrialAdapter,Hold,sensor_gate,home_gate,frame_gate,vector,bounds_gate
from paced_tracking import plan_actions,TrajectoryCursor,ProgressMonitor,fault

class PacedTrialAdapter(FirstTrialAdapter):
    def connect(self):
        if self.physical:raise fault('FIELD_VALIDATION_REQUIRED','보간 후보는 모의 전용; 실기 연결 금지')
        super().connect()
    def execute_stage(self,stage):
        if stage!='first_vla_motion' or not self.prepared:raise Hold('미준비 단계')
        limits=self.limits;start=self.clock();deadline=start+limits.max_seconds
        frames=self.await_job(self.io.capture_frames,deadline,1.)
        self.previous_hash=frame_gate(frames,self.clock(),limits)
        state=sensor_gate(self.io.snapshot(),limits,starting=True);home_gate(state,self.home)
        oldest=min(row['host_received'] for row in frames.values());inference_start=self.clock()
        actions=self.await_job(lambda:self.io.predict(frames,state),deadline,50/limits.fps)
        if self.clock()-oldest>50/limits.fps:raise fault('POLICY_LATENCY','입력 나이/추론 지연 초과')
        self.policy_chunk=dict(raw_actions=actions,observation_state=state,inference_seconds=self.clock()-inference_start)
        quantum=self.io.motor_quantum()
        self.paced_plan=plan_actions(state,actions,self.bounds,limits,quantum)
        cursor=TrajectoryCursor(self.paced_plan,limits,self.bounds,quantum);progress=ProgressMonitor(quantum)
        previous=state[:];last_sent=None;self.attempts=[];self.step_timings=[];self.paced_end_reason='COMMAND_BUDGET'
        for step in range(limits.max_steps):
            if cursor.complete:self.paced_end_reason='PATH_COMPLETE';break
            if self.stop.is_set() or self.clock()>=deadline:raise fault('DEADLINE','보간 실행 시간/정지 상한')
            work_started=self.clock()
            if step%5==0:
                frames=self.await_job(self.io.capture_frames,deadline,1.)
                self.previous_hash=frame_gate(frames,self.clock(),limits,self.previous_hash)
            measured=sensor_gate(self.io.snapshot(),limits);sampled=self.clock()
            if step==0:home_gate(measured,self.home)
            self.io.check_records();progress.observe(self.clock(),measured,previous)
            due=None if last_sent is None else last_sent+1/limits.fps
            dt=1/limits.fps if last_sent is None else max(self.clock(),due)-last_sent
            proposal=cursor.propose(measured,previous,dt)
            self.last_target_context=dict(step=step,raw_index=proposal['point']['raw_index'],planned_index=cursor.index,
                raw_action=proposal['point']['raw_action'],waypoint=proposal['point']['waypoint'],target=proposal['target'],measured_state=measured,previous_target=previous)
            if due is not None:self.sleep(max(0,due-self.clock()))
            if self.stop.is_set() or self.clock()>=deadline:raise fault('DEADLINE','전송 직전 시간/정지 상한')
            if self.clock()-sampled>1/limits.fps+1e-9:raise fault('STATE_AGE','전송 전 관측 지연 초과')
            attempt=dict(step=step,requested=proposal['target'][:],status='ATTEMPTED');self.attempts.append(attempt)
            sent_at=self.clock()
            try:returned=self.io.send_target(proposal['target'])
            except BaseException as error:attempt.update(status='ERROR',error_kind=type(error).__name__);raise
            wire=returned.get('wire_normalized') if isinstance(returned,dict) else proposal['target']
            attempt.update(status='SDK_RETURNED',wire_target=wire)
            row=dict(step=step,raw_action=proposal['point']['raw_action'],target=proposal['target'],state=measured,
                wire_target=wire,clip=proposal['target']!=proposal['point']['raw_action'],sent_at=sent_at,
                planned_index=cursor.index,raw_index=proposal['point']['raw_index'])
            self.sent.append(row)
            wire=vector(wire);bounds_gate(wire,self.bounds,source='paced_wire')
            for i in range(6):
                if abs(wire[i]-measured[i])>(limits.cap if i<5 else 2.)+1e-7 or abs(wire[i]-previous[i])>(limits.speed*dt if i<5 else 2.)+1e-7:
                    raise fault('WIRE_TARGET_MISMATCH','정수 목표 이동/속도 계약 불일치; 추가 쓰기 금지',joint=i)
            advanced=cursor.acknowledge(proposal,wire)
            self.step_timings.append(dict(step=step,sent_at=sent_at,step_seconds=self.clock()-work_started,dt=dt,
                state_age_seconds=sent_at-sampled,waypoint_advanced=advanced))
            previous=wire[:];last_sent=sent_at
        if cursor.complete:self.paced_end_reason='PATH_COMPLETE'
        self.delivered_points=cursor.index
        self.events.append('paced_'+self.paced_end_reason.lower())
