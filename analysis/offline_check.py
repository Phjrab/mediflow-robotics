"""녹화3회차의 시작·중간·후반을 저장 전처리 그대로 검증한다."""
import json
import time
from pathlib import Path
import numpy as np
from eval_blue import episodes,frames_for,CAMERAS
from config_runtime import load
from trial_runtime import static_context
from stream_live import make_policy

def main():
    c=load();context=static_context();out=Path(__file__).parents[1]/'reports/20261112'
    cfg=json.loads((context['checkpoint']/'config.json').read_text())
    train=json.loads((context['checkpoint']/'train_config.json').read_text())['policy']
    names=['use_amp','num_steps','num_vlm_layers','vlm_model_name','chunk_size','n_action_steps','normalization_mapping','resize_imgs_with_padding']
    matching={k:{'checkpoint':cfg.get(k),'training':train.get(k),'same':cfg.get(k)==train.get(k)} for k in names}
    assert all(v['same'] for v in matching.values()),matching
    policy=make_policy(context);results=[]
    try:
        for root,info,episode,rows in list(episodes()):
            offsets=[0,len(rows)//2,max(0,len(rows)-50)]
            frames=frames_for(root,info,episode,rows,offsets);errors=[];first=[];lat=[]
            policy.reset()
            for offset in offsets:
                policy.seed(101+offset)
                predicted,seconds=policy({k:{'pixels':v} for k,v in frames[offset].items()},rows[offset]['observation.state'],offset,0)
                actual=np.asarray([r['action'] for r in rows[offset:offset+50]])
                predicted=np.asarray(predicted)[:len(actual)]
                errors.extend(np.abs(predicted-actual).tolist());first.append(np.abs(predicted[0]-actual[0]).tolist());lat.append(seconds)
                if offset==0:
                    from PIL import Image
                    preview=out/'training_preview';preview.mkdir(exist_ok=True)
                    for role,pixels in frames[offset].items():Image.fromarray(pixels).save(preview/f'{episode["episode_index"]}_{role}.png')
            results.append({'episode':episode['episode_index'],'observations':len(offsets),'compared_actions':len(errors),
                            'chunk_mae':np.mean(errors,axis=0).tolist(),'first_action_mae':np.mean(first,axis=0).tolist(),'inference_s':lat})
    finally:policy.close()
    result={'task':c['task'],'config_comparison':matching,'rtc':False,'preprocessing':'saved processors, rename once, RGB640x480 /255, state degree×5 + gripper0..100','episodes':results}
    with (out/'MODEL_AUDIT.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result),flush=True);return 0
if __name__=='__main__':raise SystemExit(main())
