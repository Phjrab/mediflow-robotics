"""저장된 프레임·관절만으로 정책 평가. 로봇·카메라·serial을 열지 않는다."""
import argparse
import json
import os
from pathlib import Path
import statistics
import time
from artifacts import save_json,save_csv,temperatures
import policy_api as api

ROOT=Path(__file__).resolve().parent
def limit_chunk(actions,state):
    previous=list(map(float,state));clipped=0
    for index,action in enumerate(actions):
        try:sent=api.safe_target(list(map(float,action)),previous,previous,0.5,30.,20.)
        except ValueError as exc:
            return dict(nominal_execution_seconds=len(actions)/30.,simulation_blocked_step=index,
                reason=str(exc),clip_steps=clipped,simulation='입력 범위 오류; 전송 제한 계산도 거부')
        if any(abs(float(action[i])-sent[i])>1e-5 for i in range(6)):clipped+=1
        previous=sent
    return dict(nominal_execution_seconds=len(actions)/30.,clip_steps=clipped,
                max_body_degrees_per_second=0.5*30.,simulation='관절이 전송 목표를 완벽히 따른다는 가정의 제한 계산이며 실기 실행 아님')

def main():
    p=argparse.ArgumentParser();p.add_argument('--samples',type=Path,default=ROOT/'reports/offline_samples')
    p.add_argument('--out',type=Path,default=ROOT/'reports/offline_eval');p.add_argument('--benchmark-repeats',type=int,default=12)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import numpy as np
    import torch
    import psutil
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    checkpoint=Path.home()/'smolvla_model/pretrained_model'
    api.verify_units(Path.home()/'units_so101.json',checkpoint,Path.home()/'.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json')
    metadata=api.inspect_checkpoint(checkpoint)
    if metadata['checkpoint_kind']!='SO101_FINETUNED_CANDIDATE':raise ValueError('체크포인트 종류 미확인')
    manifest=json.loads((args.samples/'manifest.json').read_text());samples=manifest['samples']
    if not samples:raise ValueError('평가 표본 없음')
    started=time.monotonic();load_start=time.monotonic()
    policy=SmolVLAPolicy.from_pretrained(str(checkpoint),local_files_only=True).to('cuda').eval()
    if policy.config.chunk_size!=50 or policy.config.n_action_steps!=50:raise ValueError('50/50 설정 불일치')
    pre,post=make_pre_post_processors(policy.config,str(checkpoint),preprocessor_overrides={'device_processor':{'device':'cuda'}})
    load_seconds=time.monotonic()-load_start
    torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
    def observation(sample):
        data=np.load(args.samples/(sample['id']+'.npz'))
        packed={'observation.state':torch.tensor(data['state'][None],dtype=torch.float32)}
        for camera in api.CAMERAS:
            image=data[camera].copy();packed['observation.images.'+camera]=torch.from_numpy(image).permute(2,0,1).unsqueeze(0).float()/255.
        return packed,data
    def predict(packed,task,seed=101):
        torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
        torch.cuda.synchronize();start=time.perf_counter()
        actions=np.asarray(api.infer_chunk(policy,pre,post,dict(packed,task=[task]),torch),dtype=np.float64)
        torch.cuda.synchronize();elapsed=time.perf_counter()-start
        return actions,elapsed
    first=samples[0];packed,_=observation(first);timings=[]
    for i in range(max(10,args.benchmark_repeats)):
        _,elapsed=predict(packed,first['task'])
        timings.append(dict(repetition=i,seconds=elapsed,rss_bytes=psutil.Process().memory_info().rss,
            gpu_allocated_bytes=torch.cuda.memory_allocated(),gpu_peak_bytes=torch.cuda.max_memory_allocated(),
            jetson_temperatures=json.dumps(temperatures(),ensure_ascii=False)))
        save_csv(args.out/'latency.csv',timings)
        print(json.dumps({'benchmark':i,'seconds':round(elapsed,4)}),flush=True)
    metrics=[];all_actions=[];errors=[];limits=[];predictions={}
    for sample in samples:
        try:
            packed,data=observation(sample);actions,elapsed=predict(packed,sample['task']);truth=np.asarray(data['actions'],dtype=np.float64)
            difference=actions-truth;predictions[sample['id']]=actions
            for j,name in enumerate(api.JOINTS):
                metrics.append(dict(sample=sample['id'],color=sample['color'],episode_index=sample['episode_index'],phase=sample['phase'],
                    joint=name,unit='percent_0_100' if j==5 else 'degree',mae=float(np.abs(difference[:,j]).mean()),
                    max_abs_error=float(np.abs(difference[:,j]).max()),inference_seconds=elapsed))
            for step in range(50):
                for j,name in enumerate(api.JOINTS):all_actions.append(dict(sample=sample['id'],step=step,joint=name,predicted=float(actions[step,j]),recorded=float(truth[step,j]),error=float(difference[step,j])))
            limits.append(dict(sample=sample['id'],**limit_chunk(actions,data['state'])))
            save_csv(args.out/'joint_errors.csv',metrics);save_csv(args.out/'action_chunks.csv',all_actions)
            save_json(args.out/'chunk_limits.json',limits)
            print(json.dumps({'offline_sample':sample['id'],'done':True,'seconds':round(elapsed,4)}),flush=True)
        except Exception as exc:
            errors.append(dict(sample=sample['id'],error=type(exc).__name__,reason=str(exc)))
            print(json.dumps({'offline_sample':sample['id'],'error':type(exc).__name__}),flush=True)
    language=[];language_actions=[]
    cases=[next(s for s in samples if s['phase']=='start'),next(s for s in samples if s['phase']=='middle')]
    for case in cases:
        packed,_=observation(case);values={}
        for color in ('red','green','blue'):
            values[color],elapsed=predict(packed,f'Put medicine bottle A into the {color} basket',seed=101)
            for step in range(50):
                for j,name in enumerate(api.JOINTS):language_actions.append(dict(sample=case['id'],task_color=color,step=step,joint=name,action=float(values[color][step,j])))
        control,_=predict(packed,'Put medicine bottle A into the red basket',seed=101)
        for a,b in (('red','green'),('red','blue'),('green','blue'),('red','red_repeat')):
            difference=values[a]-(control if b=='red_repeat' else values[b])
            for j,name in enumerate(api.JOINTS):language.append(dict(sample=case['id'],pair=a+'__'+b,joint=name,
                mean_abs_delta=float(np.abs(difference[:,j]).mean()),max_abs_delta=float(np.abs(difference[:,j]).max()),seed=101))
        save_csv(args.out/'language_effect.csv',language);save_csv(args.out/'language_chunks.csv',language_actions)
    summary=dict(model_load_seconds=load_seconds,latency_first=timings[0]['seconds'],
        latency_mean=statistics.fmean(t['seconds'] for t in timings),latency_warm_mean=statistics.fmean(t['seconds'] for t in timings[1:]),
        latency_max=max(t['seconds'] for t in timings),repetitions=len(timings),gpu_peak_bytes=torch.cuda.max_memory_allocated(),
        rss_bytes=psutil.Process().memory_info().rss,system_available_bytes=psutil.virtual_memory().available,
        samples_evaluated=len(metrics)//6,errors=errors,jetson_temperatures=temperatures(),
        dtype=str(next(policy.parameters()).dtype),scope='학습 포함 표본의 오프라인 오차이며 성공률 아님',
        real_time_state_prediction='BLOCKED_SERIAL_STOP',register_writes=0,robot_execution='NOT_RUN',elapsed_seconds=time.monotonic()-started)
    save_json(args.out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
