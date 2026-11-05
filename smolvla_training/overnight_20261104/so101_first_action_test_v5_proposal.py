"""v5 안전 판정 제안. 장치 실행기가 아니며 어떤 레지스터도 쓰지 않는다."""
from collections import deque
from dataclasses import dataclass
import math
import policy_api as old

@dataclass(frozen=True)
class Profile:
    fps:float=30.
    step_cap:float=0.5
    speed_limit:float=20.
    first_action_limit:float=10.
    start_temperature:float=50.
    stop_temperature:float=55.
    chunk_steps:int=50

class Supervisor:
    def __init__(self,profile=Profile(),clip_window=20,max_clip_ratio=.5):
        old.rate_config(profile.fps,profile.step_cap,profile.speed_limit)
        if profile.fps!=30 or profile.chunk_steps!=50 or not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (profile.first_action_limit,profile.start_temperature,profile.stop_temperature)):
            raise ValueError('모델/데이터 주기 불일치 또는 프로파일 수치 미확인')
        if profile.step_cap>.5 or profile.speed_limit>20 or profile.first_action_limit>10 or profile.start_temperature>50 or profile.stop_temperature>55:
            raise ValueError('첫 시험 안전 기준을 완화하는 프로파일 거부')
        if not isinstance(clip_window,int) or clip_window<1 or not 0<max_clip_ratio<=1:
            raise ValueError('clip 감시 설정 오류')
        self.profile=profile;self.clips=deque(maxlen=clip_window);self.max_clip_ratio=max_clip_ratio;self.stopped=False
    def hold(self,reason):
        self.stopped=True
        return dict(decision='HOLD_NO_WRITE',reason=reason,action=None,register_writes=0)
    def propose(self,action,state,previous,torque,modes,temperatures,home,*,
                model_hash_ok=False,units_ok=False,camera_mapping_ok=False,live_confirmed=False,
                first=True,stop_requested=False,elapsed=0.,max_seconds=5.,
                observation_age=None,max_observation_age=None,inference_seconds=None,loads=None,statuses=None,
                joint_bounds=None,bounds_evidence=None):
        p=self.profile
        if self.stopped:return self.hold('이미 정지 상태; 자동 재개 없음')
        if type(first) is not bool or type(stop_requested) is not bool:return self.hold('시작/정지 상태 미확인')
        if stop_requested:return self.hold('정지 요청/Ctrl+C/예외')
        if not all(not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v) for v in (elapsed,max_seconds)) or elapsed<0 or not 0<max_seconds<=5 or elapsed>=max_seconds:
            return self.hold('시간 상한 또는 시간 값 미확인')
        if not all(v is True for v in (model_hash_ok,units_ok,camera_mapping_ok,live_confirmed)):
            return self.hold('모델/단위/카메라/현장 LIVE 확인 미충족')
        try:
            values=old.vector(list(action));measured=old.vector(list(state));previous=old.vector(list(previous))
            if not isinstance(bounds_evidence,dict) or bounds_evidence.get('status') not in ('SYNTHETIC_FIXTURE','VERIFIED_PHYSICAL_TRAVEL') or not isinstance(bounds_evidence.get('source'),str) or not bounds_evidence['source'].strip() or joint_bounds is None or len(joint_bounds)!=6:
                raise ValueError('관절 한계 근거 미확인')
            for i,bound in enumerate(joint_bounds):
                if len(bound)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in bound) or bound[0]>=bound[1]:
                    raise ValueError('관절 한계 형식 오류')
                if not bound[0]<=values[i]<=bound[1] or not bound[0]<=measured[i]<=bound[1] or not bound[0]<=previous[i]<=bound[1]:
                    raise ValueError('action/현재/이전목표가 관절 한계 밖')
            if any(abs(values[i]-measured[i])>10 or abs(values[i]-previous[i])>10 for i in range(6)):
                raise ValueError('첫 step 포함 큰 점프; clip으로 감추지 않음')
            if len(torque)!=6 or len(modes)!=6 or len(temperatures)!=6:raise ValueError('상태 차원 오류')
            if any(type(v) is not int or v!=1 for v in torque):raise ValueError('토크OFF/일부ON/미확인; 목표 쓰기 금지')
            if any(type(v) is not int or v!=0 for v in modes):raise ValueError('위치 모드0 아님')
            if any(isinstance(t,bool) or not math.isfinite(t) or t<0 or t>=p.stop_temperature for t in temperatures):raise ValueError('55도 정지 또는 온도 미확인')
            if first and any(t>=p.start_temperature for t in temperatures):raise ValueError('시작50도 미만 조건 실패')
            if loads is None or statuses is None or len(loads)!=6 or len(statuses)!=6 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>=200 for v in loads) or any(type(v) is not int or v!=0 for v in statuses):
                raise ValueError('부하/Status 미확인 또는 기준 초과')
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (max_observation_age,observation_age)) or max_observation_age<=0 or observation_age<0 or observation_age>max_observation_age:
                raise ValueError('관측 신선도 기준/값 미확인 또는 초과')
            if not isinstance(inference_seconds,(int,float)) or not math.isfinite(inference_seconds) or inference_seconds<0 or inference_seconds>p.chunk_steps/p.fps:
                raise ValueError('추론이 chunk 소비시간 초과; 외삽/연속 전송 금지')
            if first:
                if not isinstance(home,dict) or isinstance(home.get('tolerance_deg'),bool) or not isinstance(home.get('tolerance_deg'),(int,float)) or not 0<home['tolerance_deg']<=5:
                    raise ValueError('HOME 허용범위5도 조건 미확인/완화 거부')
                old.home_gate(measured,home);old.first_action_gate(values,measured,p.first_action_limit)
            sent=old.safe_target(values,measured,previous,p.step_cap,p.fps,p.speed_limit)
        except (ValueError,RuntimeError,TypeError,KeyError,IndexError,OverflowError) as exc:return self.hold(str(exc))
        self.clips.append(any(abs(sent[i]-values[i])>1e-6 for i in range(6)))
        if len(self.clips)==self.clips.maxlen and sum(self.clips)/len(self.clips)>self.max_clip_ratio:
            return self.hold('정책-제어 불일치: 최근 clip 비율 초과')
        return dict(decision='VALIDATED_TARGET_ONLY',action=sent,clipped=self.clips[-1],register_writes=0,
                    hardware_authorized=False,bounds_evidence_status=bounds_evidence['status'],
                    note='순수 계산 결과; RobotController/현장 승인 없이 전송하는 기능 없음')

if __name__=='__main__':
    raise SystemExit('PROPOSAL_ONLY: 장치 실행기 아님. live/serial 실행 기능 없음')
