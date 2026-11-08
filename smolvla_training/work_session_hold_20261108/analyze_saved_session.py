"""저장 JSON만 분석한다. 하드웨어·정책·네트워크 모듈을 사용하지 않는다."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')

def quantile(values,p):
    ordered=sorted(values)
    if not ordered:return None
    at=(len(ordered)-1)*p;low=math.floor(at);high=math.ceil(at)
    return ordered[low]+(ordered[high]-ordered[low])*(at-low)

def analyze(result,chunks):
    rows=result['targets']
    if not rows:raise ValueError('목표 기록 없음')
    for row in rows:
        for name in ('state','target','raw_action'):
            v=row[name]
            if len(v)!=6 or any(not isinstance(x,(float,int)) or isinstance(x,bool) or not math.isfinite(x) for x in v):
                raise ValueError('유한한6축 기록 필요')
    joints=[]
    for i,name in enumerate(JOINTS):
        state=[r['state'][i] for r in rows];target=[r['target'][i] for r in rows];raw=[r['raw_action'][i] for r in rows]
        errors=[abs(a-b) for a,b in zip(target,state)]
        raw_errors=[abs(a-b) for a,b in zip(raw,state)]
        joints.append(dict(joint=name,unit='degree' if i<5 else 'percent_0_100',
            observed_change=state[-1]-state[0],observed_span=max(state)-min(state),
            requested_target_span=max(target)-min(target),raw_policy_span=max(raw)-min(raw),
            requested_minus_current_mean_abs=statistics.mean(errors),requested_minus_current_p95_abs=quantile(errors,.95),
            requested_minus_current_max_abs=max(errors),raw_minus_current_max_abs=max(raw_errors),
            clipped_steps=sum(abs(a-b)>1e-6 for a,b in zip(raw,target))))
    context=(result.get('error',{}).get('observed') or {}).get('target_context') or result.get('last_target_context')
    offenders=[]
    if context:
        for i,name in enumerate(JOINTS):
            measured=abs(context['raw_action'][i]-context['measured_state'][i]);previous=abs(context['raw_action'][i]-context['previous_target'][i])
            if max(measured,previous)>10:
                offenders.append(dict(joint=name,unit='degree' if i<5 else 'percent_0_100',raw=context['raw_action'][i],
                    current=context['measured_state'][i],previous=context['previous_target'][i],raw_current_difference=measured,
                    raw_previous_difference=previous,threshold=10))
    chunk_summary=[]
    offset=0
    for c in chunks:
        pred=c.get('prediction') or {};count=c['sent_count'];part=rows[offset:offset+count];offset+=count
        elapsed=part[-1]['sent_at']-part[0]['sent_at'] if len(part)>1 else 0
        chunk_summary.append(dict(chunk=c['chunk'],status=c['status'],sent_count=count,
            inference_seconds=pred.get('inference_seconds'),cumulative_elapsed_seconds=c['elapsed_seconds'],
            command_hz=(len(part)-1)/elapsed if elapsed>0 else None))
    if offset!=len(rows):raise ValueError('구간별 전송 수 불일치')
    return dict(status=result['status'],sent_count=len(rows),torque_register_writes=result['torque_register_writes'],
        clipped_steps=sum(bool(r['clip']) for r in rows),clip_fraction=sum(bool(r['clip']) for r in rows)/len(rows),
        final_observation_is_before_last_command=True,ports_closed=result['ports_closed'],
        cleanup_errors=result['cleanup_errors'],stop_reason=result.get('error',{}).get('reason'),offending_joints=offenders,
        joints=joints,chunks=chunk_summary,successful_placement='UNVERIFIED',mechanical_obstruction='UNVERIFIED',
        servo_deadband_or_gain='UNVERIFIED',sdk_returned_action_recorded=False,
        note='target은 SDK 호출 전 요청값이며 send_action 반환값은 기록되지 않았다.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    result_path=args.root/'RESULT.json';chunk_path=args.root/'session_chunks.json'
    report=analyze(json.loads(result_path.read_text(encoding='utf-8')),json.loads(chunk_path.read_text(encoding='utf-8')))
    report['source_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (result_path,chunk_path)}
    args.out.mkdir(parents=True,exist_ok=True)
    with (args.out/'ANALYSIS.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with (args.out/'joint_tracking.csv').open('x',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(report['joints'][0]));writer.writeheader();writer.writerows(report['joints'])
    print(json.dumps({k:report[k] for k in ('sent_count','clipped_steps','stop_reason','offending_joints')},ensure_ascii=False))

if __name__=='__main__':main()
