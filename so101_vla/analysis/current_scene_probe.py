"""Fixed-noise camera/state/language interventions. Saved RGB only; devices denied."""
import argparse,json,sys,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--images-only',action='store_true');a=p.parse_args()
    from process_policy import deny_hardware
    sys.addaudithook(deny_hardware)
    import numpy as np
    import torch
    from PIL import Image,ImageDraw
    from process_policy import RealEngine
    from config_runtime import load,contract
    from observation_contract import pack_observation
    from grasp_diagnosis import select_episodes,TASKS,JOINTS,CAMERAS,write_json
    from eval_blue import frames_for
    c=load();a.out.mkdir(exist_ok=True,parents=True);data=select_episodes(a.root);blue=[x for x in data if x['mapping']['color']=='blue']
    current={role:np.asarray(Image.open(a.out/f'live_{role}.png').convert('RGB')) for role in CAMERAS}
    state_record=json.loads((a.out/'current_state.json').read_text());current_state=state_record['state']
    references=[]
    for e in blue:
        frames=frames_for(e['root'],e['info'],e['episode'],e['rows'],[0])[0]
        references.append(frames)
    first=blue[0];reference=references[0]
    comparisons={};canvas=Image.new('RGB',(1280,3*510),'white');draw=ImageDraw.Draw(canvas)
    import cv2
    for i,role in enumerate(CAMERAS):
        train,live=reference[role],current[role]
        canvas.paste(Image.fromarray(train),(0,i*510+25));canvas.paste(Image.fromarray(live),(640,i*510+25))
        draw.text((10,i*510+6),f'Training episode {first["mapping"]["merged_episode_index"]} | {role}',fill='black')
        draw.text((650,i*510+6),'Current RGB | '+role,fill='black')
        row=dict(training_mean_rgb=train.mean((0,1)).tolist(),current_mean_rgb=live.mean((0,1)).tolist(),
            training_mean_brightness=float(train.mean()),current_mean_brightness=float(live.mean()),shape=list(live.shape),mode='RGB')
        if hasattr(cv2,'aruco'):
            best=None
            for name in ('DICT_4X4_50','DICT_5X5_100','DICT_6X6_250','DICT_ARUCO_ORIGINAL'):
                detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco,name)))
                def markers(image):
                    corners,ids,_=detector.detectMarkers(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY))
                    return {} if ids is None else {int(j):xy.reshape(4,2) for j,xy in zip(ids.flatten(),corners)}
                x,y=markers(train),markers(live);common=sorted(set(x)&set(y)&set(range(8)))
                if best is None or len(common)>len(best[3]):best=(name,x,y,common)
            name,x,y,common=best
            row['markers']=dict(dictionary=name,training_ids=sorted(x),current_ids=sorted(y),common_board_ids=common)
            if len(common)>=3:
                src=np.concatenate([x[j] for j in common]);dst=np.concatenate([y[j] for j in common]);H,mask=cv2.findHomography(src,dst,cv2.RANSAC,3)
                row['markers'].update(median_raw_corner_displacement_px=float(np.median(np.linalg.norm(src-dst,axis=1))),
                    homography_train_to_current=None if H is None else H.tolist(),inlier_corners=int(mask.sum()) if mask is not None else 0)
                overlay=Image.fromarray(live);pen=ImageDraw.Draw(overlay)
                for j in common:
                    for points,color in ((x[j],(0,120,255)),(y[j],(255,70,0))):
                        pen.line([tuple(map(float,p)) for p in [*points,points[0]]],fill=color,width=2)
                overlay.save(a.out/f'alignment_{role}.png')
        comparisons[role]=row
    canvas.save(a.out/'training_current_cameras.png')
    write_json(a.out/'camera_comparison.json',comparisons)
    all_reference_alignment=[]
    fixed_detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    def fixed_markers(image):
        corners,ids,_=fixed_detector.detectMarkers(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY))
        return {} if ids is None else {int(j):xy.reshape(4,2) for j,xy in zip(ids.flatten(),corners)}
    for e in data:
        reference_images=frames_for(e['root'],e['info'],e['episode'],e['rows'],[0])[0]
        record=dict(episode=int(e['mapping']['merged_episode_index']),color=e['mapping']['color'],cameras={})
        for role in ('ceiling_vertical','ceiling_oblique'):
            x,y=fixed_markers(reference_images[role]),fixed_markers(current[role]);common=sorted(set(x)&set(y)&set(range(8)))
            values=dict(common_board_ids=common,
                training_centers={j:x[j].mean(0).tolist() for j in common},current_centers={j:y[j].mean(0).tolist() for j in common})
            if common:values['median_corner_displacement_px']=float(np.median(np.linalg.norm(np.concatenate([x[j] for j in common])-np.concatenate([y[j] for j in common]),axis=1)))
            record['cameras'][role]=values
        all_reference_alignment.append(record)
        Image.fromarray(reference_images['ceiling_oblique']).save(a.out/f'training_episode_{record["episode"]}_oblique.png')
    write_json(a.out/'all_episode_camera_alignment.json',all_reference_alignment)
    if a.images_only:
        print(json.dumps(comparisons,ensure_ascii=False));return
    engine=RealEngine({'checkpoint':str(Path(c['checkpoint']).expanduser())})
    def predict(frames,state,task,seed=101):
        engine.call('reset',None);engine.call('seed',seed)
        obs=pack_observation(frames,state,task,torch,contract(c));payload=dict(task=obs['task'],tensors={})
        for k,v in obs.items():
            if torch.is_tensor(v):payload['tensors'][k]=dict(shape=list(v.shape),bytes=v.contiguous().numpy().tobytes())
        begin=time.monotonic();actions=np.asarray(engine.call('predict',payload),dtype=float)
        return actions,time.monotonic()-begin
    cases=[];reference_predictions=[];actual_predictions=[]
    for e,frames in zip(blue,references):
        training_state=e['rows'][0]['observation.state'];truth=np.asarray([r['action'] for r in e['rows'][:50]])
        predictions={}
        for label,pixels,state in [('training_image_training_state',frames,training_state),('current_image_training_state',current,training_state),
            ('training_image_current_state',frames,current_state),('current_image_current_state',current,current_state)]:
            actions,seconds=predict(pixels,state,TASKS['blue']);predictions[label]=actions
            np.save(a.out/f'case_{e["mapping"]["merged_episode_index"]}_{label}.npy',actions)
            cases.append(dict(episode=int(e['mapping']['merged_episode_index']),case=label,seconds=seconds,
                first50_recording_mae=np.mean(np.abs(actions-truth),axis=0).tolist(),
                predicted_min=actions.min(0).tolist(),predicted_max=actions.max(0).tolist(),predicted_last=actions[-1].tolist()))
        reference_predictions.append(predictions['training_image_training_state']);actual_predictions.append(predictions['current_image_current_state'])
        ref=predictions['training_image_training_state']
        for r in cases[-4:]:
            r['difference_from_same_episode_reference_mae']=np.mean(np.abs(predictions[r['case']]-ref),axis=0).tolist()
    language={};pairwise=[]
    single_camera_interventions=[]
    truth=np.asarray([r['action'] for r in first['rows'][:50]])
    for role in CAMERAS:
        pixels=dict(current);pixels[role]=reference[role]
        actions,seconds=predict(pixels,current_state,TASKS['blue'])
        single_camera_interventions.append(dict(replaced_with_training=role,seconds=seconds,
            first50_recording_mae=np.mean(np.abs(actions-truth),axis=0).tolist(),actions=actions.tolist()))
    for seed in (101,102,103):
        sample={}
        for color in ('red','green','blue'):
            actions,seconds=predict(current,current_state,TASKS[color],seed);sample[color]=actions
            language[f'{color}_{seed}']=dict(task=TASKS[color],seconds=seconds,actions=actions.tolist())
        for left,right in [('red','green'),('red','blue'),('green','blue')]:
            diff=np.abs(sample[left]-sample[right]);pairwise.append(dict(seed=seed,pair=left+'_'+right,
                mae_per_joint=diff.mean(0).tolist(),max_per_joint=diff.max(0).tolist()))
    # Keep original inputs and interpretation explicit: first50 from a home frame
    # is only a local forecast, not a demonstration of successful closed-loop grasp.
    result=dict(status='COMPLETE',cases=cases,language_pairwise=pairwise,camera_comparison=comparisons,
        single_camera_interventions=single_camera_interventions,
        current_state=state_record,physical_motor_writes=0,hardware_opened=False,
        limitation='Current home-frame 50-action forecast covers 1.667 model seconds; it cannot prove a later 4-9s grasp or uniquely prove slow tracking.')
    write_json(a.out/'scene_probe.json',result);write_json(a.out/'language_actions.json',language)
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
