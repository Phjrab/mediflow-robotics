"""저장 데이터만 사용하는 정밀도 비교. 기본은 계획 출력, 하드웨어 제어 기능 없음."""
import argparse
import contextlib
from collections import Counter
import json
import math
import os
from pathlib import Path
import statistics
import time
from artifacts import save_json,save_csv,temperatures
import observation_contract as observation
import policy_api as api

ROOT=Path(__file__).resolve().parent
MODES=('no_autocast','cuda_fp16','cuda_bf16')


def validate_chunk(chunk):
    if len(chunk)!=50:raise ValueError('50개 action 필요')
    return [api.vector(list(row)) for row in chunk]


def deltas(reference,candidate):
    reference=validate_chunk(reference);candidate=validate_chunk(candidate)
    return [dict(joint=name,unit='degree' if j<5 else 'percent_0_100',
                 mean_abs_delta=sum(abs(candidate[i][j]-reference[i][j]) for i in range(50))/50,
                 max_abs_delta=max(abs(candidate[i][j]-reference[i][j]) for i in range(50)))
            for j,name in enumerate(api.JOINTS)]


def precision_context(torch,mode):
    if mode=='no_autocast':return torch.autocast(device_type='cuda',enabled=False)
    if mode not in MODES:raise ValueError('정밀도 모드 미확인')
    return torch.autocast(device_type='cuda',dtype=torch.float16 if mode=='cuda_fp16' else torch.bfloat16)


def output_path(path):
    path=Path(path).resolve()
    if (ROOT/'reports').resolve() not in path.parents:raise ValueError('출력은 도구 reports 아래 새 폴더만 허용')
    if path.exists():raise ValueError('기존 출력 폴더 덮어쓰기 거부')
    return path


def run(args):
    out=output_path(args.out)
    if any(p.resolve()==out or p.resolve() in out.parents for p in (args.checkpoint,args.samples)):
        raise ValueError('원본 체크포인트/표본 경로 아래 출력 금지')
    if not (args.checkpoint/'model.safetensors').is_file():raise ValueError('실제 체크포인트 가중치 없음')
    if not 5<=args.warm_repeats<=20:raise ValueError('warm 반복은 5~20회')
    contract=observation.load_contract(args.camera_config);mapping=observation.saved_mapping(args.checkpoint)
    metadata=api.inspect_checkpoint(args.checkpoint)
    if metadata['checkpoint_kind']!='SO101_FINETUNED_CANDIDATE':raise ValueError('파인튜닝 모델 미확인')
    config=json.loads((args.checkpoint/'config.json').read_text(encoding='utf-8'))
    if config.get('n_obs_steps')!=1 or config.get('chunk_size')!=50 or config.get('n_action_steps')!=50:raise ValueError('관측/행동 chunk 계약 불일치')
    manifest=json.loads((args.samples/'manifest.json').read_text(encoding='utf-8'))
    selected=[next(s for s in manifest['samples'] if s['color']==color and s['phase']=='start') for color in ('red','green','blue')]
    if any(Path(s['id']).name!=s['id'] for s in selected):raise ValueError('표본 경로 형식 오류')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import numpy as np
    import torch
    import psutil
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    if not torch.cuda.is_available():raise RuntimeError('CUDA 없음; 오프라인 GPU 비교 미실행')
    api.verify_units(Path.home()/'units_so101.json',args.checkpoint,Path.home()/'.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json')
    out.mkdir(parents=True,exist_ok=False)
    loading=time.perf_counter();policy=SmolVLAPolicy.from_pretrained(str(args.checkpoint),local_files_only=True).to('cuda').eval()
    pre,post=make_pre_post_processors(policy.config,str(args.checkpoint),preprocessor_overrides={'device_processor':{'device':'cuda'}})
    load_seconds=time.perf_counter()-loading
    batches={}
    for sample in selected:
        with np.load(args.samples/(sample['id']+'.npz'),allow_pickle=False) as data:
            frames={name:data[name].copy() for name in api.CAMERAS}
            batches[sample['id']]=observation.pack_observation(frames,data['state'].tolist(),sample['task'],torch,contract)
    modes=[];timings=[];parity=[];actions=[];baseline={};fatal=False
    def predict(mode,batch):
        torch.manual_seed(101);torch.cuda.manual_seed_all(101);torch.cuda.synchronize();start=time.perf_counter()
        with precision_context(torch,mode):chunk=api.infer_chunk(policy,pre,post,batch,torch)
        torch.cuda.synchronize();return validate_chunk(chunk),time.perf_counter()-start
    for mode in MODES:
        if fatal:break
        if mode=='cuda_bf16' and not torch.cuda.is_bf16_supported():
            modes.append(dict(mode=mode,status='NOT_RUN_UNSUPPORTED_BFLOAT16'));continue
        try:
            torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();values=[]
            for repetition in range(args.warm_repeats+1):
                _,seconds=predict(mode,batches[selected[0]['id']]);values.append(seconds)
                timings.append(dict(mode=mode,repetition=repetition,seconds=seconds,warm=repetition>0))
            for sample in selected:
                chunk,seconds=predict(mode,batches[sample['id']])
                if mode=='no_autocast':baseline[sample['id']]=chunk
                for row in deltas(baseline[sample['id']],chunk):parity.append(dict(mode=mode,sample=sample['id'],**row))
                for step,row in enumerate(chunk):
                    for joint,value in zip(api.JOINTS,row):actions.append(dict(mode=mode,sample=sample['id'],step=step,joint=joint,action=value))
            modes.append(dict(mode=mode,status='MEASURED',first_seconds=values[0],warm_mean=statistics.fmean(values[1:]),
                              warm_max=max(values[1:]),warm_median=statistics.median(values[1:]),warm_repeats=args.warm_repeats,
                              peak_gpu_bytes=torch.cuda.max_memory_allocated(),rss_bytes=psutil.Process().memory_info().rss,
                              system_available_bytes=psutil.virtual_memory().available,jetson_temperatures=temperatures(),
                              chunk_consumption_seconds=50/30,within_nominal_consumption=all(v<=50/30 for v in values[1:])))
        except Exception as error:
            modes.append(dict(mode=mode,status='ERROR',error=type(error).__name__,reason=str(error).replace(str(Path.home()),'~')))
            fatal=mode=='no_autocast' or 'illegal memory' in str(error).lower()
        finally:
            torch.clear_autocast_cache();torch.cuda.empty_cache()
        save_csv(out/'latency.csv',timings);save_csv(out/'action_deltas.csv',parity);save_csv(out/'chunks.csv',actions)
        save_json(out/'partial_modes.json',modes)
        print(json.dumps(dict(mode=mode,result=modes[-1]['status']),ensure_ascii=False),flush=True)
    result=dict(model_load_seconds=load_seconds,parameter_dtypes=dict(Counter(str(p.dtype) for p in policy.parameters())),
                saved_use_amp=config.get('use_amp'),modes=modes,camera_mapping=mapping,seed=101,
                serial_stop_present=(ROOT/'SERIAL_STOP.json').is_file(),state_source='RECORDED_DATASET_ONLY',
                current_robot_state='NOT_READ',hardware_execution='NOT_RUN',register_writes=0,
                deployment_precision_changed=False,limitation='오프라인 수치/지연이며 실기 성공률 아님. 자동 정밀도 선택 없음')
    save_json(out/'SUMMARY.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-offline',action='store_true')
    parser.add_argument('--checkpoint',type=Path,default=observation.CHECKPOINT)
    parser.add_argument('--camera-config',type=Path,default=observation.CONFIG)
    parser.add_argument('--samples',type=Path,default=ROOT/'reports/offline_samples')
    parser.add_argument('--out',type=Path,default=ROOT/'reports'/('precision_'+str(time.time_ns())))
    parser.add_argument('--warm-repeats',type=int,default=5);args=parser.parse_args()
    if not args.run_offline:
        print(json.dumps(dict(status='OFFLINE_PLAN_ONLY',modes=MODES,hardware_execution='NOT_RUN',policy_inference='NOT_RUN')));return
    run(args)


if __name__=='__main__':main()
