"""실행기와 같은 카메라 경로로 RGB3장을 저장. serial은 열지 않는다."""
import json
from pathlib import Path
from config_runtime import load,contract
from stream_live import device_class,frame_brightness
from frame_compare import compare

def main():
    from PIL import Image
    import numpy as np
    root=Path(__file__).resolve().parents[1];out=root/'reports/20261112/camera_check';out.mkdir()
    c=load();ctx={'contract':contract(c),'home':c['home']}
    def log(row):
        with (out/'events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    io=device_class()(ctx,log,out)
    result={}
    try:
        io.open_cameras(lambda:None)
        frames=io.camera_frames()
        if frames is None:raise RuntimeError('세 카메라의 최신 프레임 없음')
        for role,row in frames.items():
            pixels=row['pixels'];Image.fromarray(pixels).save(out/f'live_{role}.png')
            ref=np.asarray(Image.open(root/'analysis/vla_v3_blue_eval_preview'/f'training_{role}.png').convert('RGB'))
            result[role]={'shape':list(pixels.shape),'mode':'RGB','current':frame_brightness(pixels),'training':frame_brightness(ref)}
        compare(root/'analysis/vla_v3_blue_eval_preview',out,out/'comparison.png',current_label='Current RGB')
        result['camera_stats']=io.cameras_stats.summary()
    finally:io.close()
    result['physical_motor_writes']=0;result['serial_opened']=False;result['cleanup_errors']=io.cleanup_errors
    with (out/'summary.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
