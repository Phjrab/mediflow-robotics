"""저장된 정책 예측을 재생하는 소프트웨어 검증. 실제 제어/새 추론 기능 없음."""
import argparse
from collections import Counter,deque
import csv
import hashlib
import json
import math
from pathlib import Path
import time
from artifacts import save_json,save_csv
from evaluate_offline import limit_chunk
import policy_api as api
from software_preflight import inspect_software
from so101_first_action_test_v5_proposal import Supervisor

ROOT=Path(__file__).resolve().parent


class InMemoryRobot:
    physical=False
    def __init__(self,state):
        self.state=api.vector(list(state));self.stopped=False;self.closed=False;self.state_updates=0;self.register_writes=0
    def simulate_target(self,target):
        if self.stopped or self.closed:raise RuntimeError('모의 backend 정지 상태')
        self.state=api.vector(list(target));self.state_updates+=1
    def hold(self):self.stopped=True
    def close(self):self.closed=True


class SoftwareOnlyController:
    def __init__(self,backend):
        if type(backend) is not InMemoryRobot:raise ValueError('실제/외부 backend 연결 금지')
        self.backend=backend;self.guard=Supervisor();self.steps=0
    def step(self,packet):
        if self.backend.closed or packet.get('first',self.steps==0) is not (self.steps==0):
            result=self.guard.hold('모의 제어 시작 단계 불일치/이미 닫힘')
        else:
            try:result=self.guard.propose(**dict(packet,first=self.steps==0))
            except (Exception,KeyboardInterrupt):result=self.guard.hold('예외/Ctrl+C; 목표 쓰기 없음')
        if result['decision']=='VALIDATED_TARGET_ONLY':self.backend.simulate_target(result['action'])
        else:self.backend.hold()
        self.steps+=1
        return result
    def close(self):self.backend.close()


def read_predictions(path,field='predicted'):
    if field not in ('predicted','recorded'):raise ValueError('예측/녹화 action 열만 허용')
    found={}
    with Path(path).open(encoding='utf-8',newline='') as stream:
        for row in csv.DictReader(stream):
            sample=row['sample'];step=int(row['step']);joint=row['joint'];value=float(row[field])
            if not 0<=step<50 or joint not in api.JOINTS or not math.isfinite(value):raise ValueError('예측 CSV 인덱스/수치 오류')
            key=(sample,step,joint)
            if key in found:raise ValueError('예측 CSV 중복 행')
            found[key]=value
    samples={key[0] for key in found};chunks={}
    for sample in samples:
        if sum(key[0]==sample for key in found)!=300:raise ValueError('표본의50×6 action 누락')
        chunks[sample]=[api.vector([found[(sample,step,j)] for j in api.JOINTS]) for step in range(50)]
    return chunks


def clip_diagnostic(chunk,state):
    # 미래의 측정값이 아니라 완벽 추종을 가정한 순수 제한 계산이다.
    previous=list(state);window=deque(maxlen=20);first_stop=None
    for step,target in enumerate(chunk):
        candidate=api.safe_target(target,previous,previous,.5,30.,20.)
        window.append(any(abs(a-b)>1e-6 for a,b in zip(target,candidate)))
        if first_stop is None and len(window)==20 and sum(window)/20>.5:first_stop=step
        previous=candidate
    return dict(first_clip_ratio_stop_step=first_stop,**limit_chunk(chunk,state))


def run(args):
    out=args.out.resolve();protected=[args.checkpoint.resolve(),args.manifest.parent.resolve()]
    if (ROOT/'reports').resolve() not in out.parents or any(p==out or p in out.parents for p in protected) or out.exists():
        raise ValueError('원본/기존 경로 출력 금지; reports 아래 새 폴더 필요')
    software=inspect_software(args.checkpoint,args.camera_config)
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'));samples=manifest['samples']
    if len({s['id'] for s in samples})!=len(samples):raise ValueError('표본 ID 중복')
    chunks=read_predictions(args.predictions)
    recorded_chunks=read_predictions(args.predictions,'recorded')
    if set(chunks)!={s['id'] for s in samples}:raise ValueError('예측과 표본 manifest 불일치')
    timings={}
    with args.timings.open(encoding='utf-8',newline='') as stream:
        for row in csv.DictReader(stream):
            value=float(row['inference_seconds'])
            if not math.isfinite(value) or value<0:raise ValueError('기록 지연 오류')
            if row['sample'] in timings and timings[row['sample']]!=value:raise ValueError('회차 내 지연 기록 불일치')
            timings[row['sample']]=value
    out.mkdir(parents=True,exist_ok=False);rows=[]
    for sample in samples:
        name=sample['id'];state=api.vector(list(sample['state']));chunk=chunks[name]
        if sample['task']!=api.COLOR_TASKS[sample['color']]:raise ValueError('task/색 라벨 불일치')
        latency=timings[name];backend=InMemoryRobot(state);controller=SoftwareOnlyController(backend)
        try:
            # 실제 토크/온도/HOME/현장승인 값을 만들어 넣지 않는다.
            result=controller.step(dict(action=chunk[0],state=state,previous=state,torque=[],modes=[],temperatures=[],
                home=None,model_hash_ok=False,units_ok=False,camera_mapping_ok=True,live_confirmed=False,
                inference_seconds=latency))
        finally:controller.close()
        if backend.register_writes or backend.state_updates:raise RuntimeError('현장 확인 없이 모의 목표도 적용되지 않아야 함')
        diagnostic=clip_diagnostic(chunk,state)
        recorded_diagnostic=clip_diagnostic(recorded_chunks[name],state)
        rows.append(dict(sample=name,color=sample['color'],phase=sample['phase'],task=sample['task'],
            recorded_inference_seconds=latency,latency_gate_pass=latency<=50/30,
            first_action_max_body_delta=max(abs(chunk[0][i]-state[i]) for i in range(5)),
            clip_steps=diagnostic['clip_steps'],first_clip_ratio_stop_step=diagnostic['first_clip_ratio_stop_step'],
            recorded_clip_steps=recorded_diagnostic['clip_steps'],recorded_first_clip_ratio_stop_step=recorded_diagnostic['first_clip_ratio_stop_step'],
            controller_decision=result['decision'],reason=result['reason'],register_writes=0,state_updates=backend.state_updates))
    report=dict(status='CACHED_REPLAY_COMPLETE_NOT_HARDWARE_READY',samples=len(rows),
                colors=dict(Counter(r['color'] for r in rows)),latency_gate_failed=sum(not r['latency_gate_pass'] for r in rows),
                clip_diagnostic_stop_candidates=sum(r['first_clip_ratio_stop_step'] is not None for r in rows),
                recorded_action_clip_stop_candidates=sum(r['recorded_first_clip_ratio_stop_step'] is not None for r in rows),
                controller_hold_count=sum(r['controller_decision']=='HOLD_NO_WRITE' for r in rows),
                software_preflight=software,register_writes=0,hardware_execution='NOT_RUN',
                actual_policy_inference='NOT_RUN_REUSING_PREVIOUS_PREDICTIONS',current_robot_state='NOT_READ',
                source_hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in [('predictions',args.predictions),('manifest',args.manifest),('timings',args.timings)]},
                limit_diagnostic='완벽 추종 가정의 제한 계산. 지연/현장 gate를 해제하거나 모터에 보내지 않음')
    save_csv(out/'replay.csv',rows);save_json(out/'SUMMARY.json',report)
    print(json.dumps({k:report[k] for k in ('status','samples','latency_gate_failed','clip_diagnostic_stop_candidates','recorded_action_clip_stop_candidates','controller_hold_count','register_writes')},ensure_ascii=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,default=Path.home()/'smolvla_model/pretrained_model')
    parser.add_argument('--camera-config',type=Path,default=ROOT/'vla_observation_config.json')
    parser.add_argument('--predictions',type=Path,default=ROOT/'reports/offline_eval_v2/action_chunks.csv')
    parser.add_argument('--timings',type=Path,default=ROOT/'reports/offline_eval_v2/joint_errors.csv')
    parser.add_argument('--manifest',type=Path,default=ROOT/'reports/offline_samples/manifest.json')
    parser.add_argument('--out',type=Path,default=ROOT/'reports'/('cached_replay_'+str(time.time_ns())))
    run(parser.parse_args())


if __name__=='__main__':main()
