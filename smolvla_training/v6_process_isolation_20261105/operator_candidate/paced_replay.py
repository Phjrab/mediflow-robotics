"""저장 정책 행동과 가상 관절로만 보간 후보를 검증한다. 실기 옵션 없음."""
import argparse
import copy
import json
from pathlib import Path
from first_trial_core import JOINTS,CAMERAS,FirstMotionPlan,Hold
from work_segment_limits import WorkSegmentLimits
from paced_trial import PacedTrialAdapter
from controller_bridge import load_controller
from run_receipts import json_value

class Clock:
    def __init__(self):self.now=1.
    def __call__(self):return self.now
    def sleep(self,seconds):self.now+=seconds

class SimulatedIO:
    physical=False
    def __init__(self,prediction,kind='ideal'):
        self.clock=Clock();self.prediction=prediction;self.kind=kind;self.closed=False;self.commands=[];self.frame=0
        self.state=list(prediction['observation_state']);self.target=self.state[:]
        self.quantum=prediction.get('motor_quantum',[360/4095]*5+[.0612])
    def connect(self):pass
    def close(self):self.closed=True
    def snapshot(self):
        return dict(state=self.state[:],torque=dict.fromkeys(JOINTS,1),modes=dict.fromkeys(JOINTS,0),
            temperatures=dict.fromkeys(JOINTS,30.),loads=dict.fromkeys(JOINTS,0.),statuses=dict.fromkeys(JOINTS,0),moving=False)
    def health_snapshot(self):return self.snapshot()
    def check_records(self):pass
    def capture_frames(self):
        self.frame+=1
        return {k:dict(host_received=self.clock(),shape=[480,640,3],dtype='uint8',sha256=str(self.frame)+k) for k in CAMERAS}
    def predict(self,frames,state):
        self.clock.sleep(self.prediction.get('inference_seconds',0.))
        return copy.deepcopy(self.prediction['raw_actions'])
    def motor_quantum(self):return self.quantum[:]
    def send_target(self,target):
        self.commands.append(target[:]);self.target=target[:]
        if self.kind=='ideal':self.state=target[:]
        elif self.kind=='slow':self.state=[v+.2*(t-v) for v,t in zip(self.state,target)]
        return dict(wire_normalized=target[:])

def immediate(fn,monitor,**kwargs):monitor();value=fn();monitor();return value

def run_case(prediction,bounds,kind):
    hw=SimulatedIO(prediction,kind)
    evidence=dict(kind='FIXTURE',**dict.fromkeys(('model_verified','units_verified','mapping_verified','operator_live_confirmed','arm_receipt_verified'),True))
    home=dict(joint_names=list(JOINTS),state=hw.state[:],tolerance_deg=5.)
    adapter=PacedTrialAdapter(hw,evidence,home,bounds,limits=WorkSegmentLimits(),waiter=immediate,clock=hw.clock,sleep=hw.clock.sleep)
    result=dict(status='HOLD',simulation=True,plant=kind,hardware_access=False,motor_register_writes=0,placement_success='UNVERIFIED')
    try:
        load_controller(mock_posix=True)(adapter).run(FirstMotionPlan(False))
        result.update(status='MOCK_PACED_'+adapter.paced_end_reason,delivered_points=adapter.delivered_points)
    except BaseException as error:result['error']=dict(kind=type(error).__name__,reason=str(error),observed=getattr(error,'observed',None))
    finally:adapter.close()
    result.update(closed=hw.closed,simulated_commands=len(hw.commands),elapsed_seconds=hw.clock()-1.,
        final_simulated_state=hw.state,targets=adapter.sent,attempts=getattr(adapter,'attempts',[]),
        plan=getattr(adapter,'paced_plan',None),events=adapter.events)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--session-chunks',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();chunks=json.loads(args.session_chunks.read_text(encoding='utf-8'));args.out.mkdir(parents=True,exist_ok=False)
    summary=[]
    for chunk in chunks:
        prediction=chunk.get('prediction')
        if prediction is None:continue
        for kind in ('ideal','slow','stationary'):
            result=run_case(prediction,prediction['bounds'],kind);name=f"chunk_{chunk['chunk']:02d}_{kind}.json"
            with (args.out/name).open('x',encoding='utf-8') as f:json.dump(json_value(result),f,ensure_ascii=False,allow_nan=False,indent=2)
            summary.append(dict(chunk=chunk['chunk'],plant=kind,status=result['status'],simulated_commands=result['simulated_commands'],
                closed=result['closed'],error_code=result.get('error',{}).get('observed',{}).get('code') if result.get('error',{}).get('observed') else None))
    with (args.out/'SUMMARY.json').open('x',encoding='utf-8') as f:json.dump(dict(cases=summary,hardware_access=False,inference='NOT_RUN',
        note='가상 관절 결과이며 실제 모터 응답이나 성공률이 아니다.'),f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=len(summary),ports_mock_closed=all(r['closed'] for r in summary),hardware_access=False),ensure_ascii=False))

if __name__=='__main__':main()
