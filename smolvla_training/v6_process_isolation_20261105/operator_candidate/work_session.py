"""하나의 연결·정책으로 유한한 작업 구간을 이어 실행한다."""
from first_trial_core import Hold
from first_trial import SupervisedAdapter

class SessionStages:
    def execute_stage(self,stage):
        if self.limits.max_steps!=50 or self.limits.max_seconds>10 or self.limits.cap>2:raise Hold("세션 작업 프로필 불일치")
        started=self.clock();self.session_chunks=[];all_timings=[];self.session_end_reason="MAX_CHUNKS"
        if getattr(self,"data_session",False):self.session_deadline=started+30
        for chunk in range(6):
            remaining=30-(self.clock()-started)
            if (remaining<=0 if getattr(self,"data_session",False) else remaining<self.limits.max_seconds):
                self.session_end_reason="TIME_BUDGET";break
            if self.stop.is_set():raise Hold("세션 정지 요청")
            self.skip_home_recheck=chunk>0
            before=len(self.sent)
            if hasattr(self,"policy_chunk"):del self.policy_chunk
            self.step_timings=[]
            complete=False
            try:
                super().execute_stage(stage);complete=True
            finally:
                for i,row in enumerate(self.sent[before:],before):row["step"]=i
                for row in getattr(self,"step_timings",[]):
                    row["chunk"]=chunk;row["step"]+=before;all_timings.append(row)
                self.session_chunks.append(dict(chunk=chunk,status="COMPLETE" if complete else "HOLD",
                    sent_count=len(self.sent)-before,elapsed_seconds=self.clock()-started,
                    prediction=getattr(self,"policy_chunk",None)))
                self.step_timings=list(all_timings)
            print(f"작업 구간 {chunk+1}/6 완료: 누적 목표 {len(self.sent)}개",flush=True)
        if not self.session_chunks:raise Hold("세션에서 완료한 구간 없음")
        self.events.append("session_"+self.session_end_reason.lower())

class WorkSessionAdapter(SessionStages,SupervisedAdapter):
    session_max_steps=300
    session_max_seconds=30
