"""저장된 목표 계측과 후속 관측을 대조한다. 하드웨어 접근 없음."""
import argparse
import json
from pathlib import Path
import statistics
from first_trial_core import JOINTS,vector

def summarize(result,telemetry=None):
    targets=result.get('targets',[])
    output=dict(recorded_targets=len(targets),telemetry_available=telemetry is not None,
        motor_fault='UNVERIFIED',friction_or_deadband='UNVERIFIED',placement_success='UNVERIFIED',joints=[],error_attempts=[])
    if telemetry is None:
        output['status']='TELEMETRY_MISSING';return output
    if any(type(row.get('index')) is not int or row['index']!=i for i,row in enumerate(telemetry)):raise ValueError('계측 인덱스 순서 불일치')
    sdk_differences=[[] for _ in JOINTS];integer_differences=[[] for _ in JOINTS];tracking=[[] for _ in JOINTS];next_intervals=[]
    for i,row in enumerate(telemetry):
        request=vector(row['requested'])
        if row['status']!='SDK_RETURNED':
            output['error_attempts'].append(dict(index=i,status=row['status'],error_kind=row.get('error_kind')));continue
        sdk=vector(row['sdk_returned']);wire=vector(row['wire_normalized'])
        if i>=len(targets):raise ValueError('SDK 반환과 저장 목표 수 불일치')
        if any(abs(a-b)>1e-9 for a,b in zip(request,vector(targets[i]['target']))):raise ValueError('요청 목표 연결 불일치')
        for j in range(6):sdk_differences[j].append(abs(sdk[j]-request[j]));integer_differences[j].append(abs(wire[j]-sdk[j]))
        if i+1<len(targets):
            state=vector(targets[i+1]['state']);dt=targets[i+1]['sent_at']-targets[i]['sent_at']
            if dt<=0:raise ValueError('목표 시각 역행')
            next_intervals.append(dt)
            for j in range(6):tracking[j].append(abs(wire[j]-state[j]))
    for j,name in enumerate(JOINTS):
        output['joints'].append(dict(joint=name,unit='degree' if j<5 else 'percent_0_100',
            sdk_additional_clip_count=sum(x>1e-6 for x in sdk_differences[j]),
            sdk_change_max=max(sdk_differences[j],default=None),encoding_change_max=max(integer_differences[j],default=None),
            next_observation_error_mean=statistics.mean(tracking[j]) if tracking[j] else None,
            next_observation_error_max=max(tracking[j],default=None),paired_samples=len(tracking[j])))
    output.update(status='GOAL_TRACKING_ANALYZED',next_observation_interval_max=max(next_intervals,default=None),
        final_post_command_observation='UNVERIFIED',note='후속 관측 간격은 추론 대기를 포함할 수 있다. 추종 오차만으로 모터 고장이나 마찰을 단정하지 않는다.')
    return output

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    result=json.loads((args.run/'RESULT.json').read_text(encoding='utf-8'));path=args.run/'goal_telemetry.json'
    telemetry=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    report=summarize(result,telemetry);args.out.mkdir(parents=True,exist_ok=False)
    with (args.out/'GOAL_TRACKING.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({key:report[key] for key in ('status','recorded_targets','telemetry_available','motor_fault')},ensure_ascii=False))

if __name__=='__main__':main()
