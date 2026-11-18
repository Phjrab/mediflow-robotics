"""Saved RGB only: compare aligned, previous and training views at fixed states."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    from process_policy import deny_hardware
    sys.addaudithook(deny_hardware)
    import numpy as np
    import torch
    from PIL import Image
    from alignment_metrics import compare_markers,detect_markers
    from config_runtime import load,contract
    from observation_contract import pack_observation
    from process_policy import RealEngine
    from grasp_diagnosis import JOINTS,CAMERAS,TASKS,write_json
    baseline=args.root/'reports/20261115/GRASP_CAUSE_7_EPISODES'
    c=load();args.out.mkdir(parents=True,exist_ok=True)
    if (args.out/'recheck.json').exists():raise RuntimeError('완료된 평가를 덮어쓰지 않음')
    saved=np.load(baseline/'full_frame_evaluation/episode_93_actions.npz')
    recorded=saved['recorded'];truth=recorded[:50];training_state=saved['state'][0].tolist()
    old_state=json.loads((baseline/'current_state.json').read_text())['state']
    states={'training_episode_start':training_state,'fixed_previous_home_read':old_state,'nominal_home':c['home']['state']}
    views={
        'training':{r:np.asarray(Image.open(baseline/'full_frame_evaluation'/f'training_first_{r}.png').convert('RGB')) for r in CAMERAS},
        'previous':{r:np.asarray(Image.open(baseline/f'live_{r}.png').convert('RGB')) for r in CAMERAS},
        'aligned':{r:np.asarray(Image.open(args.out/f'live_{r}.png').convert('RGB')) for r in CAMERAS}}
    geometry={r:compare_markers(detect_markers(views['training'][r]),detect_markers(views['aligned'][r])) for r in CAMERAS}
    for role in CAMERAS:
        geometry[role].update(current_brightness=float(views['aligned'][role].mean()),
                              training_brightness=float(views['training'][role].mean()),
                              current_rgb_mean=views['aligned'][role].mean((0,1)).tolist(),
                              training_rgb_mean=views['training'][role].mean((0,1)).tolist())
    write_json(args.out/'alignment_geometry.json',geometry)
    write_json(args.out/'states.json',dict(states=states,current_robot_state_measured=False,
        reason='No fresh LeLab joint cache. Previous measured home and nominal home are explicitly distinguished; no serial access.'))
    engine=RealEngine({'checkpoint':str(Path(c['checkpoint']).expanduser())})
    cases=[('training','training_episode_start'),('training','fixed_previous_home_read'),
           ('previous','fixed_previous_home_read'),('aligned','fixed_previous_home_read'),
           ('training','nominal_home'),('aligned','nominal_home')]
    reports=[];begin=time.monotonic();grasp=recorded[181];range_scale=np.maximum(np.ptp(recorded,axis=0),1.)
    for view,state_key in cases:
        state=states[state_key]
        observation=pack_observation(views[view],state,TASKS['blue'],torch,contract(c))
        payload=dict(task=observation['task'],tensors={key:dict(shape=list(value.shape),bytes=value.contiguous().numpy().tobytes())
            for key,value in observation.items() if torch.is_tensor(value)})
        for seed in (101,102,103):
            engine.call('reset',None);engine.call('seed',seed);started=time.monotonic()
            actions=np.asarray(engine.call('predict',payload),dtype=float);seconds=time.monotonic()-started
            if actions.shape!=(50,6) or not np.isfinite(actions).all():raise ValueError('50x6 유한한 예측 필요')
            name=f'{view}_{state_key}_{seed}';np.save(args.out/(name+'.npy'),actions)
            with (args.out/(name+'.csv')).open('w',newline='') as target:
                writer=csv.writer(target);writer.writerow(['frame',*[f'pred_{j}' for j in JOINTS],*[f'recorded_{j}' for j in JOINTS]])
                for frame,(action,actual) in enumerate(zip(actions,truth)):writer.writerow([frame,*action,*actual])
            direction=(actions[-1]-np.asarray(state))/range_scale
            reference=(grasp-np.asarray(state))/range_scale
            norm=float(np.linalg.norm(direction)*np.linalg.norm(reference))
            row=dict(view=view,state_key=state_key,seed=seed,inference_s=seconds,
                mae_per_joint=np.mean(np.abs(actions-truth),axis=0).tolist(),
                max_error_per_joint=np.max(np.abs(actions-truth),axis=0).tolist(),
                predicted_min=actions.min(0).tolist(),predicted_max=actions.max(0).tolist(),
                predicted_last=actions[-1].tolist(),delta_from_input_state=(actions[-1]-np.asarray(state)).tolist(),
                cosine_towards_recorded_grasp=None if norm==0 else float(np.dot(direction,reference)/norm))
            reports.append(row)
            write_json(args.out/'progress.json',dict(done=len(reports),total=len(cases)*3,case=name,elapsed_s=time.monotonic()-begin))
            print(json.dumps(dict(case=name,elbow_mae=row['mae_per_joint'][2],elbow_delta=row['delta_from_input_state'][2],gripper_mae=row['mae_per_joint'][5])),flush=True)
    aggregates=[]
    for view,state_key in cases:
        rows=[r for r in reports if r['view']==view and r['state_key']==state_key]
        aggregates.append(dict(view=view,state_key=state_key,
            mean_mae_per_joint=np.mean([r['mae_per_joint'] for r in rows],axis=0).tolist(),
            mean_delta_from_input_state=np.mean([r['delta_from_input_state'] for r in rows],axis=0).tolist(),
            mean_cosine_towards_recorded_grasp=float(np.mean([r['cosine_towards_recorded_grasp'] for r in rows]))))
    result=dict(status='COMPLETE',episode=93,task=TASKS['blue'],seeds=[101,102,103],joints=JOINTS,
        geometry=geometry,states=states,cases=reports,aggregates=aggregates,elapsed_s=time.monotonic()-begin,
        current_robot_state_measured=False,physical_motor_writes=0,hardware_opened=False,
        recorded_first50_min=truth.min(0).tolist(),recorded_first50_max=truth.max(0).tolist(),
        recorded_first50_last=truth[-1].tolist(),recorded_grasp_frame=181,recorded_grasp_action=grasp.tolist(),
        limitation='Home-frame prediction covers 1.667 model seconds; it cannot measure a later 6.03s gripper closing error or prove physical grasp.')
    write_json(args.out/'recheck.json',result)
    write_json(args.out/'progress.json',dict(status='COMPLETE',done=len(reports),total=len(reports),elapsed_s=result['elapsed_s']))
    print('RECHECK_COMPLETE',str(args.out),flush=True)


if __name__=='__main__':main()
