"""원본 parquet/video를 읽어 평가 표본만 새 폴더에 복사한다. 원본은 변경하지 않는다."""
import argparse
import json
from pathlib import Path
import time
from artifacts import save_json

CAMERAS=('end_effector','ceiling_vertical','ceiling_oblique')
SOURCES={'red':('a_to_red_20261102_201029',{1}),
         'green':('a_to_green_20261102_201804',set()),
         'blue':('a_to_blue_20261103_193835',{0,3,4,9,20,34})}
JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')

def decode_nearest(path,target,fps):
    import av
    with av.open(str(path)) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=2
        container.seek(max(0,int(target/float(stream.time_base))),stream=stream,backward=True,any_frame=False)
        previous=None
        for frame in container.decode(video=0):
            if frame.pts is None:continue
            stamp=float(frame.pts*stream.time_base)
            if stamp<target:
                previous=(frame,stamp);continue
            chosen=(frame,stamp)
            if previous and abs(previous[1]-target)<abs(stamp-target):chosen=previous
            if abs(chosen[1]-target)>1.1/fps:
                raise ValueError('영상/데이터 시각 대응 오차가1프레임 초과')
            return chosen[0].to_ndarray(format='rgb24'),chosen[1]
    if previous and abs(previous[1]-target)<=1.1/fps:return previous[0].to_ndarray(format='rgb24'),previous[1]
    raise ValueError('요청 영상 프레임을 찾지 못함')

def prepare(out,per_color=3):
    import numpy as np
    import pyarrow.parquet as pq
    from PIL import Image,ImageDraw
    started=time.monotonic();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    base=Path.home()/'.cache/huggingface/lerobot/Supermassive111';samples=[];errors=[];images={}
    for color,(name,excluded) in SOURCES.items():
        root=base/name;info=json.loads((root/'meta/info.json').read_text())
        expected=[j+'.pos' for j in JOINTS]
        if info['features']['action']['names']!=expected or info['features']['observation.state']['names']!=expected:
            raise ValueError('state/action 이름 순서 불일치')
        fps=float(info['fps'])
        keys=['episode_index','tasks','length','data/chunk_index','data/file_index']
        for camera in CAMERAS:
            keys += ['videos/observation.images.'+camera+'/'+key for key in ('chunk_index','file_index','from_timestamp','to_timestamp')]
        episodes=[]
        for path in sorted((root/'meta/episodes').rglob('*.parquet')):episodes.extend(pq.read_table(path,columns=keys).to_pylist())
        selected=[e for e in sorted(episodes,key=lambda e:e['episode_index']) if e['episode_index'] not in excluded and e['length']>=100][:per_color]
        if len(selected)<per_color:raise ValueError('색별 평가 회차 부족')
        for e in selected:
            data_path=root/info['data_path'].format(chunk_index=e['data/chunk_index'],file_index=e['data/file_index'])
            table=pq.read_table(data_path,columns=['episode_index','frame_index','timestamp','observation.state','action'])
            rows=sorted((r for r in table.to_pylist() if r['episode_index']==e['episode_index']),key=lambda r:r['frame_index'])
            if len(rows)!=e['length']:raise ValueError('에피소드 데이터 수 불일치')
            for phase,offset in (('start',0),('middle',min(len(rows)-50,len(rows)//2))):
                sample_id=f"{color}_e{e['episode_index']:03d}_{phase}"
                try:
                    frames={};times={}
                    for camera in CAMERAS:
                        prefix='videos/observation.images.'+camera+'/'
                        path=root/info['video_path'].format(video_key='observation.images.'+camera,
                            chunk_index=e[prefix+'chunk_index'],file_index=e[prefix+'file_index'])
                        target=float(e[prefix+'from_timestamp'])+float(rows[offset]['timestamp'])-float(rows[0]['timestamp'])
                        frame,stamp=decode_nearest(path,target,fps)
                        if frame.shape!=(480,640,3):raise ValueError('원본 카메라 크기 불일치')
                        frames[camera]=frame;times[camera]=dict(target=target,decoded=stamp)
                    state=np.asarray(rows[offset]['observation.state'],dtype=np.float32)
                    actions=np.asarray([r['action'] for r in rows[offset:offset+50]],dtype=np.float32)
                    if actions.shape!=(50,6) or state.shape!=(6,) or not np.isfinite(actions).all() or not np.isfinite(state).all():
                        raise ValueError('action/state 수치/형식 오류')
                    np.savez_compressed(out/(sample_id+'.npz'),state=state,actions=actions,**frames)
                    task=f'Put medicine bottle A into the {color} basket'
                    if list(e['tasks'])!=[task]:raise ValueError('원본 task 문구 불일치; 임의 수정하지 않음')
                    samples.append(dict(id=sample_id,color=color,dataset=name,episode_index=e['episode_index'],offset=offset,
                        phase=phase,task=task,original_tasks=e['tasks'],fps=fps,video_times=times,
                        state=state.tolist(),included_in_training=True,exclusions_zero_based=sorted(excluded)))
                    if phase=='start' and color not in images:
                        images[color]=frames
                        for camera,frame in frames.items():Image.fromarray(frame).save(out/(color+'_'+camera+'_start.png'))
                    print(json.dumps({'sample':sample_id,'prepared':True}),flush=True)
                except Exception as exc:
                    errors.append(dict(sample=sample_id,error=type(exc).__name__,reason=str(exc)))
                    print(json.dumps({'sample':sample_id,'error':type(exc).__name__}),flush=True)
    save_json(out/'manifest.json',dict(samples=samples,errors=errors,seconds=time.monotonic()-started,
        evaluation_scope='학습에 포함된 원본 표본 재현 오차이며 held-out/성공률 아님'))
    current=Path(__file__).resolve().parent/'reports/cameras_current'
    if all(c in images for c in ('red','green','blue')) and current.exists():
        sheet=Image.new('RGB',(1280,780),'white');draw=ImageDraw.Draw(sheet);metrics=[]
        for row,camera in enumerate(CAMERAS):
            for col,color in enumerate(('red','green','blue','current')):
                frame=np.asarray(Image.open(current/(camera+'.png'))) if color=='current' else images[color][camera]
                panel=Image.fromarray(frame).resize((320,240));sheet.paste(panel,(col*320,row*260+20))
                draw.text((col*320+6,row*260+4),f'{col+1}  camera{row+1}',fill='black')
                metrics.append(dict(camera=camera,column=col+1,source=color,mean_rgb=frame.mean(axis=(0,1)).tolist(),
                    brightness_rgb_mean=float(frame.mean()),std_rgb=frame.std(axis=(0,1)).tolist(),black_fraction=float((frame.max(axis=2)==0).mean())))
        sheet.save(out/'camera_comparison.png');save_json(out/'camera_comparison_metrics.json',metrics)
    print(json.dumps({'PREPARED':len(samples),'errors':len(errors),'seconds':time.monotonic()-started}),flush=True)
    return samples

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'reports/offline_samples');p.add_argument('--per-color',type=int,default=3)
    a=p.parse_args();prepare(a.out,a.per_color)
