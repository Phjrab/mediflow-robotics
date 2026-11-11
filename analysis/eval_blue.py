"""Evaluate both checkpoint modes against three included blue-basket episodes."""
import argparse
import json
import os
from pathlib import Path
import time

from direct_live import JOINTS, TASK

CAMERAS = ('end_effector', 'ceiling_vertical', 'ceiling_oblique')
EXCLUDED = {0, 3, 4, 9, 20, 34}


def episodes():
    import pyarrow.parquet as pq

    root = Path.home()/'.cache/huggingface/lerobot/Supermassive111/a_to_blue_20261103_193835'
    info = json.loads((root/'meta/info.json').read_text())
    for key in ('action', 'observation.state'):
        if info['features'][key]['names'] != [j+'.pos' for j in JOINTS]:
            raise ValueError('unexpected joint order')
    assert info['fps'] == 30
    candidates = [e for f in (root/'meta/episodes').rglob('*.parquet')
                  for e in pq.read_table(f).to_pylist()
                  if e['episode_index'] not in EXCLUDED and list(e['tasks']) == [TASK] and e['length'] >= 100]
    for e in sorted(candidates, key=lambda x: (x['length'], x['episode_index']))[:3]:
        path = root/info['data_path'].format(chunk_index=e['data/chunk_index'], file_index=e['data/file_index'])
        table = pq.read_table(path, columns=['episode_index', 'frame_index', 'timestamp', 'observation.state', 'action'])
        rows = sorted((r for r in table.to_pylist() if r['episode_index']==e['episode_index']),key=lambda r:r['frame_index'])
        if len(rows)!=e['length']:
            raise ValueError('episode frame count mismatch')
        yield root, info, e, rows


def frames_for(root, info, episode, rows, offsets):
    import av

    selected = {offset: {} for offset in offsets}
    for camera in CAMERAS:
        key = 'observation.images.' + camera
        prefix = 'videos/'+key+'/'
        path = root/info['video_path'].format(video_key=key, chunk_index=episode[prefix+'chunk_index'],
                                              file_index=episode[prefix+'file_index'])
        start = float(episode[prefix+'from_timestamp'])
        targets = iter((i, start + float(rows[i]['timestamp'])-float(rows[0]['timestamp'])) for i in offsets)
        current = next(targets, None)
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 2
            container.seek(max(0, int(start / float(stream.time_base))), stream=stream, backward=True)
            for frame in container.decode(video=0):
                if frame.pts is None:
                    continue
                timestamp = float(frame.pts * stream.time_base)
                if current and timestamp >= current[1] - 0.5/30:
                    if abs(timestamp-current[1]) > 0.55/30:
                        raise ValueError(f'{camera} frame timestamp mismatch at {current}')
                    rgb = frame.to_ndarray(format='rgb24')
                    if rgb.shape != (480, 640, 3):
                        raise ValueError('frame resolution mismatch')
                    selected[current[0]][camera] = rgb
                    current = next(targets, None)
                    if current is None:
                        break
        if current is not None:
            raise ValueError(f'{camera} missing frames')
    return selected


def main():
    from PIL import Image
    import numpy as np
    from trial_runtime import static_context

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stride', type=int, default=1)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.stride < 1:
        parser.error('positive stride required')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    args.out.mkdir(exist_ok=True, parents=True)
    context = static_context()
    from stream_policy import StreamingPolicy
    data = list(episodes())
    assert len(data) == 3
    totals = {}
    # Run one model process at a time to stay within Jetson memory.
    for rtc in (False, True):
        label = 'rtc' if rtc else 'checkpoint'
        policy = StreamingPolicy(context, TASK, rtc=rtc)
        errors, reports = [], []
        try:
            assert policy.transport.config['stream_rtc'] is rtc
            for root, info, episode, rows in data:
                policy.reset()
                episode_id = episode['episode_index']
                episode_errors, latencies = [], []
                for begin in range(0,len(rows),32*args.stride):
                    offsets = list(range(begin,min(len(rows),begin+32*args.stride),args.stride))
                    frames = frames_for(root,info,episode,rows,offsets)
                    if begin == 0 and not rtc and episode_id == data[0][2]['episode_index']:
                        for camera, pixels in frames[0].items():
                            Image.fromarray(pixels).save(args.out/f'training_{camera}.png')
                    for offset in offsets:
                        policy.seed(101 + offset)
                        chunk, seconds = policy({k:{'pixels':v} for k,v in frames[offset].items()},
                                                rows[offset]['observation.state'],offset,30)
                        error = np.abs(np.asarray(chunk[0])-np.asarray(rows[offset]['action']))
                        errors.append(error)
                        episode_errors.append(error)
                        latencies.append(seconds)
                reports.append({'episode':episode_id,'frames':len(rows),'evaluated':len(episode_errors),
                                'mae_per_joint':np.mean(episode_errors,axis=0).tolist(),
                                'mean_inference_s':float(np.mean(latencies))})
                print(json.dumps({'mode':label,**reports[-1]}),flush=True)
        finally:
            policy.close()
        totals[label] = {'observations':len(errors),'mae_per_joint':np.mean(errors,axis=0).tolist(),'episodes':reports}
    result = {'task':TASK,'stride':args.stride,'joints':JOINTS,'results':totals}
    (args.out/'quality.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
