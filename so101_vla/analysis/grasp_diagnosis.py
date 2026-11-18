"""Seven-episode, hardware-denied teacher-forced input and action comparison."""
import argparse,csv,hashlib,json,os,sys,time
from pathlib import Path

JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')
CAMERAS=('end_effector','ceiling_vertical','ceiling_oblique')
TASKS={c:f'Put medicine bottle A into the {c} basket' for c in ('red','green','blue')}

def write_json(path,value):
    pending=path.with_suffix('.pending');pending.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');pending.replace(path)

def select_episodes(root):
    import pyarrow.parquet as pq
    mapping=list(csv.DictReader((root/'_archive/20261110/jetson/home/motion_limits_20261109_1794202196649/episode_mapping.csv').open()))
    selected=[]
    for color,n in [('blue',5),('red',1),('green',1)]:
        candidates=[r for r in mapping if r['color']==color]
        indices=[round(i*(len(candidates)-1)/(n-1)) for i in range(n)] if n>1 else [len(candidates)//2]
        selected.extend(candidates[i] for i in indices)
    result=[]
    for m in selected:
        ds=Path.home()/'.cache/huggingface/lerobot'/m['source_repo_id'];info=json.loads((ds/'meta/info.json').read_text())
        ep=int(m['source_episode_index'])
        meta=next(e for p in (ds/'meta/episodes').rglob('*.parquet') for e in pq.read_table(p).to_pylist() if e['episode_index']==ep)
        data=ds/info['data_path'].format(chunk_index=meta['data/chunk_index'],file_index=meta['data/file_index'])
        rows=sorted([r for r in pq.read_table(data).to_pylist() if r['episode_index']==ep],key=lambda r:r['frame_index'])
        # Exact historical merged trims. Never include excluded trailing rows.
        if m['source_repo_id'].endswith('a_to_red_20261102_210433') and ep==39:rows=rows[:455]
        if m['source_repo_id'].endswith('a_to_blue_20261102_212958') and ep==0:rows=rows[:516]
        if list(meta['tasks'])!=[TASKS[m['color']]]:raise ValueError('데이터 task 문자열 불일치: '+str(meta['tasks']))
        if info['fps']!=30 or any(info['features'][k]['names']!=[j+'.pos' for j in JOINTS] for k in ('action','observation.state')):
            raise ValueError('학습 단위/순서/FPS 확인 실패')
        result.append(dict(mapping=m,root=ds,info=info,episode=meta,rows=rows))
    return result

def closing_candidate(rows):
    grip=[r['action'][5] for r in rows];start=grip[0]
    opened=next((i for i,g in enumerate(grip) if g>start+10),None)
    if opened is None:return None
    peak=grip[opened]
    for i in range(opened+1,len(rows)-3):
        peak=max(peak,grip[i]);threshold=max(5,.2*(peak-start))
        if i-opened>=12 and all(grip[k]<peak-threshold for k in range(i,i+3)) and rows[i]['action'][2]<70:
            return i
    return None

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--batch-size',type=int,default=4);p.add_argument('--benchmark-only',action='store_true');a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    from process_policy import deny_hardware
    sys.addaudithook(deny_hardware)
    import numpy as np
    import torch
    from PIL import Image
    from process_policy import RealEngine
    from observation_contract import pack_observation
    from config_runtime import load,contract
    from checkpoint_amp import infer_with_saved_amp
    from lerobot.policies.utils import prepare_observation_for_inference
    from eval_blue import frames_for
    c=load();episodes=select_episodes(a.root)
    manifest=[dict(**e['mapping'],frames=len(e['rows']),task=TASKS[e['mapping']['color']],closing_frame=closing_candidate(e['rows'])) for e in episodes]
    write_json(a.out/'episode_manifest.json',manifest)
    ck=Path(c['checkpoint']).expanduser();saved=json.loads((ck/'config.json').read_text());train=json.loads((ck/'train_config.json').read_text())
    keys=('use_amp','num_steps','num_vlm_layers','chunk_size','n_action_steps','normalization_mapping','resize_imgs_with_padding','adapt_to_pi_aloha')
    matches={k:dict(training=train['policy'].get(k),runtime=saved.get(k),same=train['policy'].get(k)==saved.get(k)) for k in keys}
    if not all(row['same'] for row in matches.values()):raise RuntimeError('학습/체크포인트 설정 불일치')
    hashes={f:hashlib.sha256((ck/f).read_bytes()).hexdigest() for f in ('policy_preprocessor.json','policy_postprocessor.json',
        'policy_preprocessor_step_5_normalizer_processor.safetensors','policy_postprocessor_step_0_unnormalizer_processor.safetensors')}
    write_json(a.out/'input_contract.json',dict(config_comparison=matches,hashes=hashes,
        dataset=train['dataset'],task_exact={color:TASKS[color] for color in TASKS},camera_mapping=json.loads((ck/'policy_preprocessor.json').read_text())['steps'][0]['config']['rename_map'],
        image='RGB uint8 640x480 -> float32 BCHW /255; saved model resize_with_pad 512x512 -> [-1,1]',
        state='degree x5, gripper 0..100; saved checkpoint MEAN_STD normalizer',
        amp='checkpoint use_amp=true, CUDA autocast default float16; no weight conversion',rtc=False))
    engine=RealEngine({'checkpoint':str(ck)})
    used_keys=list(saved['input_features'])+['observation.language.tokens','observation.language.attention_mask']
    comparisons=[];batch_bench=[]
    def inputs(frames,state,task):
        run=pack_observation(frames,state,task,torch,contract(c))
        raw={'observation.state':np.asarray(state,dtype=np.float32)}
        raw.update({'observation.images.'+k:np.ascontiguousarray(v) for k,v in frames.items()})
        official=prepare_observation_for_inference(raw,torch.device('cpu'),task=task,robot_type='so101_follower')
        run=engine.pre(run);official=engine.pre(official)
        # Compare every tensor actually consumed by this installed SmolVLA.
        diff={k:float((run[k].float()-official[k].float()).abs().max().item()) for k in run if torch.is_tensor(run[k]) and k in official}
        missing=[k for k in run if torch.is_tensor(run[k]) and k not in official]
        if missing or any(diff.values()):raise RuntimeError('공식/실행기 전처리 불일치: '+str((missing,diff)))
        return run,official,diff
    def infer(batch,noise):
        torch.cuda.synchronize();begin=time.monotonic()
        with torch.inference_mode():normalized=infer_with_saved_amp(lambda:engine.policy.predict_action_chunk(dict(batch),noise=noise),torch,saved)
        actions=engine.post(normalized).detach().cpu().float().numpy()
        torch.cuda.synchronize();return normalized,actions,time.monotonic()-begin
    first=episodes[0];preview=frames_for(first['root'],first['info'],first['episode'],first['rows'],[0])
    for role,pixels in preview[0].items():Image.fromarray(pixels).save(a.out/f'training_first_{role}.png')
    one,official,diff=inputs(preview[0],first['rows'][0]['observation.state'],TASKS['blue'])
    generator=torch.Generator(device='cuda').manual_seed(101);noise=torch.randn((1,50,saved['max_action_dim']),generator=generator,device='cuda')
    _,single,t=infer(one,noise);_,reference,t2=infer(official,noise)
    official_diff=float(np.max(np.abs(single-reference)))
    if official_diff>1e-4:raise RuntimeError('동일 단일 입력 공식 추론 결과 불일치')
    for count in (1,2,4):
        try:
            batch={k:torch.cat([v]*count,dim=0) if torch.is_tensor(v) else v*count if isinstance(v,list) else v for k,v in one.items()}
            _,predicted,seconds=infer(batch,noise.repeat(count,1,1))
            batch_bench.append(dict(batch=count,seconds=seconds,seconds_per_frame=seconds/count,
                single_max_difference_deg_or_points=float(np.max(np.abs(predicted[0]-single[0]))),memory_peak_bytes=torch.cuda.max_memory_allocated()))
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache();batch_bench.append(dict(batch=count,error='OOM'));break
    write_json(a.out/'benchmark.json',dict(preprocessor_max_difference=diff,official_prediction_max_difference=official_diff,batches=batch_bench))
    # Offline batching only: report its FP16 numerical sensitivity. The live
    # runner remains batch1. Paired official comparisons use the same batch.
    batch_size=min(a.batch_size,max(r['batch'] for r in batch_bench if 'error' not in r and r['single_max_difference_deg_or_points']<=.25))
    print(json.dumps(dict(event='benchmark',batch_size=batch_size,results=batch_bench,total_frames=sum(len(e['rows']) for e in episodes))),flush=True)
    if a.benchmark_only:return
    reports=[];started=time.monotonic();done=0;all_frames=sum(len(e['rows']) for e in episodes)
    for e in episodes:
        rows=e['rows'];meta=e['mapping'];number=int(meta['merged_episode_index']);task=TASKS[meta['color']]
        engine.policy.reset();predictions=np.empty((len(rows),50,6),dtype=np.float32);times=[];max_pre=0.;max_post=0.;max_official=0.
        closure=closing_candidate(rows)
        for begin in range(0,len(rows),16):
            offsets=list(range(begin,min(len(rows),begin+16)));frames=frames_for(e['root'],e['info'],e['episode'],rows,offsets)
            for b in range(0,len(offsets),batch_size):
                indices=offsets[b:b+batch_size];packed=[];officials=[];noises=[]
                for i in indices:
                    run,off,diff=inputs(frames[i],rows[i]['observation.state'],task);packed.append(run);officials.append(off)
                    max_pre=max(max_pre,max(diff.values(),default=0.))
                    gen=torch.Generator(device='cuda').manual_seed(100000*number+101+i)
                    noises.append(torch.randn((1,50,saved['max_action_dim']),generator=gen,device='cuda'))
                def cat(items):
                    return {k:torch.cat([r[k] for r in items],dim=0) if torch.is_tensor(items[0][k]) else
                        sum([r[k] for r in items],[]) if isinstance(items[0][k],list) else items[0][k] for k in items[0]}
                batch=cat(packed);noise=torch.cat(noises,dim=0)
                normalized,predicted,seconds=infer(batch,noise);times.append(seconds/len(indices))
                # Identical model inputs share the expensive inference. Check the official
                # saved postprocessor separately for all 50 actions of every frame.
                for n,i in enumerate(indices):
                    official_actions=engine.post(normalized[n:n+1]).detach().cpu().float().numpy()[0]
                    max_post=max(max_post,float(np.max(np.abs(official_actions-predicted[n]))))
                    predictions[i]=predicted[n]
                # Independent official reinference at start/grasp/end, same batch and noise.
                if any(i in (0,closure,len(rows)-1) for i in indices):
                    _,official_prediction,_=infer(cat(officials),noise)
                    max_official=max(max_official,float(np.max(np.abs(official_prediction-predicted))))
                done+=len(indices)
                write_json(a.out/'progress.json',dict(status='RUNNING',done=done,total=all_frames,episode=number,frame=indices[-1],
                    elapsed_s=time.monotonic()-started,eta_s=(time.monotonic()-started)/done*(all_frames-done)))
        actual=np.asarray([r['action'] for r in rows],dtype=np.float32);state=np.asarray([r['observation.state'] for r in rows],dtype=np.float32)
        first_error=np.abs(predictions[:,0]-actual);chunk_errors=[]
        for i in range(len(rows)):chunk_errors.append(np.abs(predictions[i,:min(50,len(rows)-i)]-actual[i:i+50]))
        combined=np.concatenate(chunk_errors,axis=0)
        np.savez_compressed(a.out/f'episode_{number}_actions.npz',predictions=predictions,recorded=actual,state=state)
        with (a.out/f'episode_{number}_first_actions.csv').open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['frame',*[f'pred_{j}' for j in JOINTS],*[f'recorded_{j}' for j in JOINTS],*[f'error_{j}' for j in JOINTS]])
            for i in range(len(rows)):writer.writerow([i,*predictions[i,0].tolist(),*actual[i].tolist(),*first_error[i].tolist()])
        report=dict(**meta,frames=len(rows),task=task,first_action_mae=first_error.mean(0).tolist(),first_action_max=first_error.max(0).tolist(),
            chunk_mae=combined.mean(0).tolist(),chunk_max=combined.max(0).tolist(),mean_inference_s_per_frame=float(np.mean(times)),
            preprocessor_max_difference=max_pre,postprocessor_max_difference=max_post,official_reinference_max_difference=max_official,
            closing_frame=closure,closing_action=None if closure is None else actual[closure].tolist(),
            closing_prediction=None if closure is None else predictions[closure,0].tolist(),
            closing_absolute_error=None if closure is None else first_error[closure].tolist())
        reports.append(report);write_json(a.out/'quality_partial.json',reports);print(json.dumps(report),flush=True)
        if number==int(episodes[0]['mapping']['merged_episode_index']):
            write_json(a.out/'replay_episode.json',dict(**meta,task=task,fps=30,joints=JOINTS,actions=actual.tolist(),
                recorded_states=state.tolist(),source_parquet_sha256=hashlib.sha256((e['root']/e['info']['data_path'].format(chunk_index=e['episode']['data/chunk_index'],file_index=e['episode']['data/file_index'])).read_bytes()).hexdigest()))
    result=dict(status='COMPLETE',frames=done,batch_size=batch_size,episodes=reports,offline_batch_sensitivity=batch_bench,
        equivalence_method='All-frame official/runner preprocessed tensors exact and postprocessing compared; inference deduplicated for identical tensors, independent official reinference at start/grasp/end of each episode.',
        elapsed_s=time.monotonic()-started,physical_motor_writes=0)
    write_json(a.out/'quality.json',result);write_json(a.out/'progress.json',dict(status='COMPLETE',done=done,total=all_frames,elapsed_s=time.monotonic()-started))
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
