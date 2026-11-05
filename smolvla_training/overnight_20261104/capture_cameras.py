"""카메라만 열어 RGB 시작 프레임을 저장한다. follower/serial 모듈을 사용하지 않는다."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from artifacts import save_json
from observation_contract import load_contract,display_mapping

ROOT=Path(__file__).resolve().parent
DEVICES=load_contract()['cameras']

def capture(out):
    contract=load_contract();mapping=display_mapping()
    from check_lights_via_lelab import states
    from check_three_cameras import occupancy
    states()
    for name,device in contract['cameras'].items():
        if not occupancy(Path(device),name=='ceiling_vertical'):
            raise RuntimeError('카메라 점유/조회 오류; 로봇 상태는 읽지 않고 중단: '+name)
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from PIL import Image
    import numpy as np
    out=Path(out);out.mkdir(parents=True,exist_ok=False);result={};opened=[]
    try:
        for name,device in contract['cameras'].items():
            states()
            camera=OpenCVCamera(OpenCVCameraConfig(index_or_path=device,fps=30,width=640,height=480,
                color_mode='rgb',warmup_s=contract['warmup_seconds'],backend=200))
            opened.append(camera);camera.connect()
            frame=np.asarray(camera.async_read(timeout_ms=3000)).copy()
            if frame.shape!=(480,640,3) or frame.dtype!=np.uint8:raise ValueError('카메라 크기/형식 오류: '+name)
            time.sleep(0.15);next_frame=np.asarray(camera.async_read(timeout_ms=3000)).copy()
            Image.fromarray(frame).save(out/(name+'.png'))
            result[name]=dict(policy_key=mapping[name],shape=list(frame.shape),dtype=str(frame.dtype),
                mean_rgb=frame.mean(axis=(0,1)).tolist(),std_rgb=frame.std(axis=(0,1)).tolist(),
                image_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
                repeated_two_samples=bool(np.array_equal(frame,next_frame)),
                black_fraction=float((frame.max(axis=2)==0).mean()))
            camera.disconnect();opened.remove(camera)
            if not occupancy(Path(device),name=='ceiling_vertical'):
                raise RuntimeError('카메라 해제 상태 미확인: '+name)
    finally:
        for camera in reversed(opened):
            try:camera.disconnect()
            except Exception as exc:print('카메라 닫기 오류: '+type(exc).__name__,flush=True)
    save_json(out/'camera_summary.json',result);print(json.dumps({'camera_count':len(result),'out':out.name,'frames':result},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'reports'/('cameras_'+str(time.time_ns())))
    a=p.parse_args();capture(a.out)
