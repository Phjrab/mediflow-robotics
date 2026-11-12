"""실행기와 같은 카메라 경로로 RGB3장을 저장. serial은 열지 않는다."""
import json
import time
from datetime import datetime
from pathlib import Path
from config_runtime import load,contract
from stream_live import device_class,frame_brightness
from frame_compare import compare

def main():
    from PIL import Image
    import numpy as np
    root=Path(__file__).resolve().parents[1]
    out=root/'reports'/datetime.now().strftime('%Y%m%d')/f'camera_check_{time.time_ns()}'
    out.mkdir(parents=True)
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
            result[role]={'shape':list(pixels.shape),'dtype':str(pixels.dtype),'mode':'RGB',
                          'pixel_min':int(pixels.min()),'pixel_max':int(pixels.max()),
                          'current':frame_brightness(pixels),'training':frame_brightness(ref)}
        compare(root/'analysis/vla_v3_blue_eval_preview',out,out/'comparison.png',current_label='Current RGB')
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
        log({'event':'camera_check_failed',**result['error']})
    finally:io.close()
    result['camera_stats']=io.cameras_stats.summary()
    result['physical_motor_writes']=0;result['serial_opened']=False;result['cleanup_errors']=io.cleanup_errors
    result['output']=str(out)
    with (out/'summary.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result),flush=True);return 1 if 'error' in result else 0
if __name__=='__main__':raise SystemExit(main())
