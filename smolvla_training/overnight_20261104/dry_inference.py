"""쓰기 기능 없는 무인 dry. 중단 기록이 있으면 SDK/모델 로딩 전에 거부한다."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import nov_readonly as n
import policy_api as api
from artifacts import save_json,save_csv
from observation_contract import load_contract,saved_mapping,pack_observation,validate_frames

def safe_health(session,home):
    value=session.read(home);n.home_gate(value,home)
    if any(m!=0 for m in value['modes'].values()):raise n.SafetyStop('위치 모드0 불일치')
    for j in n.JOINTS:
        load=float(session.bus.read('Present_Load',j,normalize=False,num_retry=0))
        status=session.bus.read('Status',j,normalize=False,num_retry=0)
        if not math.isfinite(load) or abs(load)>=200 or status!=0:
            n.stop_serial('부하/Status 오류',dict(joint=j,load=load,status=status))
    return value

def frames_from(cameras,np):
    frames={}
    for name,camera in cameras.items():
        frame=np.asarray(camera.async_read(timeout_ms=3000)).copy()
        if frame.shape!=(480,640,3) or frame.dtype!=np.uint8:raise ValueError('카메라 RGB/크기 오류')
        if frame.max()==frame.min():raise ValueError('동일값 영상; 관측 거부')
        frames[name]=frame
    return frames

def main():
    n.check_stop()
    contract=load_contract()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    home=n.read_home();checkpoint=Path.home()/'smolvla_model/pretrained_model'
    mapping=saved_mapping(checkpoint)
    api.verify_units(Path.home()/'units_so101.json',checkpoint,n.CALIBRATION)
    if api.inspect_checkpoint(checkpoint)['checkpoint_kind']!='SO101_FINETUNED_CANDIDATE':raise ValueError('모델 종류 미확인')
    policy=SmolVLAPolicy.from_pretrained(str(checkpoint),local_files_only=True).to('cuda').eval()
    if policy.config.chunk_size!=50 or policy.config.n_action_steps!=50:raise ValueError('50/50 설정 불일치')
    pre,post=make_pre_post_processors(policy.config,str(checkpoint),preprocessor_overrides={'device_processor':{'device':'cuda'}})
    out=n.ROOT/'reports'/('dry_readonly_'+str(time.time_ns()));out.mkdir(parents=True,exist_ok=False)
    cameras={}
    try:
        with n.ReadSession() as session:
            before=safe_health(session,home);session.verify_motor_calibration()
            for name,device in contract['cameras'].items():
                camera=OpenCVCamera(OpenCVCameraConfig(index_or_path=device,fps=30,width=640,height=480,
                    color_mode='rgb',warmup_s=contract['warmup_seconds'],backend=200));cameras[name]=camera;camera.connect()
                safe_health(session,home)
            frames=frames_from(cameras,np);before=safe_health(session,home)
            validate_frames(frames,contract)
            state=api.vector([before['positions'][j] for j in n.JOINTS])
            for name,frame in frames.items():
                Image.fromarray(frame).save(out/(name+'_start.png'))
            task='Put medicine bottle A into the blue basket'
            packed=pack_observation(frames,state,task,torch,contract)
            torch.cuda.synchronize();start=time.perf_counter()
            actions=api.infer_chunk(policy,pre,post,packed,torch)
            torch.cuda.synchronize();elapsed=time.perf_counter()-start
            for action in actions:api.vector(action)
            after=safe_health(session,home);current_frames=frames_from(cameras,np)
            if any(np.array_equal(frames[k],current_frames[k]) for k in frames):raise ValueError('동일 프레임 후보; dry 통과 보류')
            rows=[dict(chunk=0,task=task,joint=j,unit='percent_0_100' if i==5 else 'degree',
                state_at_inference=state[i],first_action=actions[0][i],delta_first_action=actions[0][i]-state[i],
                inference_seconds=elapsed,mode='dry-run',executed_steps=0) for i,j in enumerate(n.JOINTS)]
            save_csv(out/'chunks.csv',rows);save_json(out/'summary.json',dict(before=before,after=after,
                inference_seconds=elapsed,register_writes=0,executed_steps=0,read_packets=session.guard.read_packets,
                camera_mapping=mapping,camera_config=contract))
            if any(abs(r['delta_first_action'])>1 for r in rows):raise ValueError('첫 목표 차이1단위 초과; 숫자 검수 거부')
            print('DRY_REVIEW_NUMERIC_PASS: 장면·색·구도는 별도 현장 확인 필요',flush=True)
    finally:
        for camera in reversed(list(cameras.values())):
            try:camera.disconnect()
            except Exception:pass
    print(json.dumps(dict(out=out.name,register_writes=0,live='NOT_RUN')),flush=True)

if __name__=='__main__':main()
