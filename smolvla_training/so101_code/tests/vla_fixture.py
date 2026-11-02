"""Tiny real parquet/shared MP4 fixture; synthetic pixels, never a camera."""
import json
from pathlib import Path
import sys
import av
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from vla_data import JOINTS, ROOT
from vla_record import load_tasks


def fixture(root, episodes=3, frames=6):
    root=Path(root); root.mkdir(parents=True)
    features={k:{"dtype":"float32","shape":[6],"names":JOINTS} for k in ("action","observation.state")}
    for k in ("timestamp","frame_index","episode_index","index","task_index"):
        features[k]={"dtype":"float32" if k=="timestamp" else "int64","shape":[1],"names":None}
    cameras=["observation.images."+s for s in ("wrist","top","oblique")]
    for k in cameras:
        features[k]={"dtype":"video","shape":[24,32,3],"names":["height","width","channels"],
                     "info":{"video.fps":30,"video.codec":"h264","video.pix_fmt":"yuv420p","video.is_depth_map":False,"has_audio":False}}
    cfg=load_tasks(ROOT/"configs/vla_tasks.yaml")
    tasks=[cfg["combinations"][c]["ko"][0] for c in ("A:red","B:blue")]
    info=dict(codebase_version="v3.0",fps=30,features=features,total_episodes=episodes,total_frames=episodes*frames,
              total_tasks=2,robot_type="so101_follower",splits={"train":f"0:{episodes}"},
              data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
              video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4")
    (root/"meta/episodes/chunk-000").mkdir(parents=True)
    (root/"data/chunk-000").mkdir(parents=True)
    (root/"meta/info.json").write_text(json.dumps(info))
    pd.DataFrame({"task_index":[0,1]},index=pd.Index(tasks,name="task")).to_parquet(root/"meta/tasks.parquet")
    rows=[]; metas=[]
    for ep in range(episodes):
        task=ep%2
        data=[dict(index=ep*frames+i,episode_index=ep,frame_index=i,timestamp=np.float32(i/30).item(),task_index=task,
                   action=[float(i+j+ep) for j in range(6)],**{"observation.state":[float(i+j+ep-.25) for j in range(6)]}) for i in range(frames)]
        rows.extend(data)
        meta=dict(episode_index=ep,length=frames,tasks=[tasks[task]],dataset_from_index=ep*frames,dataset_to_index=(ep+1)*frames)
        meta.update({"data/chunk_index":0,"data/file_index":0,"meta/episodes/chunk_index":0,"meta/episodes/file_index":0})
        for k in cameras:
            for suffix,v in (("chunk_index",0),("file_index",0),("from_timestamp",ep*frames/30),("to_timestamp",(ep+1)*frames/30)):
                meta[f"videos/{k}/{suffix}"]=v
        for key in ("action","observation.state"):
            arr=np.asarray([r[key] for r in data])
            for stat,val in dict(min=arr.min(0),max=arr.max(0),mean=arr.mean(0),std=arr.std(0),count=np.array([frames])).items():
                meta[f"stats/{key}/{stat}"]=val.tolist()
        metas.append(meta)
    pq.write_table(pa.Table.from_pylist(rows),root/"data/chunk-000/file-000.parquet")
    pq.write_table(pa.Table.from_pylist(metas),root/"meta/episodes/chunk-000/file-000.parquet")
    stats={}
    for key in ("action","observation.state"):
        arr=np.asarray([r[key] for r in rows]); stats[key]={s:v.tolist() for s,v in dict(min=arr.min(0),max=arr.max(0),mean=arr.mean(0),std=arr.std(0),count=np.array([len(rows)])).items()}
    (root/"meta/stats.json").write_text(json.dumps(stats))
    for cam,key in enumerate(cameras):
        path=root/info["video_path"].format(video_key=key,chunk_index=0,file_index=0); path.parent.mkdir(parents=True)
        with av.open(str(path),"w") as out:
            stream=out.add_stream("libx264",rate=30); stream.width=32; stream.height=24; stream.pix_fmt="yuv420p"
            for i in range(episodes*frames):
                rgb=np.zeros((24,32,3),np.uint8); rgb[:,:,cam]=(i*11+20)%255; rgb[i%24,:, (cam+1)%3]=150
                for packet in stream.encode(av.VideoFrame.from_ndarray(rgb,format="rgb24")): out.mux(packet)
            for packet in stream.encode(): out.mux(packet)
    return root


def mutate(root,kind,change):
    path=Path(root)/("data/chunk-000/file-000.parquet" if kind=="rows" else "meta/episodes/chunk-000/file-000.parquet")
    rows=pq.read_table(path).to_pylist(); change(rows); pq.write_table(pa.Table.from_pylist(rows),path)
