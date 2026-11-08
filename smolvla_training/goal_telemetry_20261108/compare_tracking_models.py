"""가상 추종 모델로 현재 목표 제한을 재현한다. 실제 모터 진단 결과가 아니다."""
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'v6_process_isolation_20261105/operator_candidate'))
from first_trial_core import target_gate,Hold
from work_segment_limits import WorkSegmentLimits

def simulate(follows):
    state=[0.,0.,96.,0.,0.,10.];previous=state[:];bounds=[(-180.,180.)]*5+[(0.,100.)]
    limits=WorkSegmentLimits();sent=0
    for step in range(161):
        action=[0.,0.,96.-step*.1,0.,0.,10.]
        try:goal=target_gate(action,state,previous,bounds,limits,1/30,quantum=[360/4095]*5+[.0612])
        except Hold as error:
            return dict(simulated=True,follows=follows,status='HOLD',reason=str(error),sent=sent,observed_elbow=state[2],raw_goal=action[2])
        previous=goal;sent+=1
        if follows:state=goal[:]
    return dict(simulated=True,follows=follows,status='COMPLETE',sent=sent,observed_elbow=state[2])

def main():
    following=simulate(True);stationary=simulate(False)
    assert following['status']=='COMPLETE' and abs(following['observed_elbow']-80)<1e-9
    assert stationary['status']=='HOLD' and stationary['reason']=='큰 목표 점프' and stationary['sent']==101
    root=HERE.parent/'work_session_hold_20261108/raw'
    result=json.loads((root/'RESULT.json').read_text(encoding='utf-8'));context=result['last_target_context'];last=result['targets'][-1]
    report=dict(simulation_note='가상 추종 성공/정지 모델이며 실제 마찰·모터 설정을 측정한 결과가 아니다.',
        ideal_tracking=following,stationary_tracking=stationary,
        field=dict(current_elbow=context['measured_state'][2],next_raw_elbow=context['raw_action'][2],
            previous_raw_elbow=last['raw_action'][2],raw_adjacent_delta=abs(context['raw_action'][2]-last['raw_action'][2]),
            nominal_raw_speed_degrees_per_second=abs(context['raw_action'][2]-last['raw_action'][2])*30,
            actual_goal_speed_limit=20,current_to_raw_gap=abs(context['raw_action'][2]-context['measured_state'][2])),
        conclusion='같은 정책 궤적도 실제 관절 추종 여부에 따라 완료 또는 현재값 차이 HOLD가 된다. 연속 예측값 차이와 현재 관절 대비 오차를 구분해야 한다.')
    path=HERE/'TRACKING_MODEL_COMPARISON.json'
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
