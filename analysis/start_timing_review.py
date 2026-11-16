"""Audit demonstration idle periods and time-shift diagnostics, without devices."""
import argparse
import csv
import json
from pathlib import Path
import sys


def motion_onset(actions, threshold=2.):
    """First three-sample departure of lift/elbow from the initial action."""
    for index in range(max(0,len(actions)-2)):
        if all(max(abs(actions[k][axis]-actions[0][axis]) for axis in (1,2))>=threshold
               for k in range(index,index+3)):
            return index
    return None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    from process_policy import deny_hardware
    sys.addaudithook(deny_hardware)
    import numpy as np
    import pyarrow.parquet as pq
    mapping=list(csv.DictReader((args.root/'_archive/20261110/jetson/home/motion_limits_20261109_1794202196649/episode_mapping.csv').open()))
    sources={};reports=[];reference=None
    for row in mapping:
        repo=row['source_repo_id'];episode=int(row['source_episode_index'])
        if repo not in sources:
            path=Path.home()/'.cache/huggingface/lerobot'/repo
            values=[r for p in (path/'data').rglob('*.parquet') for r in pq.read_table(p,columns=['episode_index','frame_index','action']).to_pylist()]
            source={}
            for r in values:source.setdefault(r['episode_index'],[]).append(r)
            sources[repo]={e:sorted(rows,key=lambda r:r['frame_index']) for e,rows in source.items()}
        frames=sources[repo][episode]
        if repo.endswith('a_to_red_20261102_210433') and episode==39:frames=frames[:455]
        if repo.endswith('a_to_blue_20261102_212958') and episode==0:frames=frames[:516]
        actions=[r['action'] for r in frames];onset=motion_onset(actions)
        reports.append(dict(episode=int(row['merged_episode_index']),color=row['color'],frames=len(actions),
                            onset_frame=onset,onset_s=None if onset is None else onset/30))
        if int(row['merged_episode_index'])==93:reference=np.asarray(actions)
    summary={}
    for color in ('red','green','blue','all'):
        values=[r['onset_frame'] for r in reports if r['onset_frame'] is not None and (color=='all' or r['color']==color)]
        summary[color]=dict(episodes=len(values),onset_frames_p50_p95_max=np.percentile(values,[50,95,100]).tolist(),
                            onset_seconds_p50_p95_max=(np.percentile(values,[50,95,100])/30).tolist())
    previous=args.root/'reports/20261116/ALIGNMENT_RECHECK';lag_diagnostics=[]
    onset=motion_onset(reference);scale=np.maximum(np.ptp(reference,axis=0),1.)
    for seed in (101,102,103):
        predicted=np.load(previous/f'aligned_nominal_home_{seed}.npy');candidates=[]
        for lag in range(onset+1):
            error=np.abs(predicted-reference[lag:lag+50]);fit=(error/scale).mean(0)
            # Fit timing using the other five axes, then evaluate held-out elbow.
            candidates.append((float(fit[[0,1,3,4,5]].mean()),lag,error.mean(0).tolist()))
        score,lag,errors=min(candidates)
        lag_diagnostics.append(dict(seed=seed,fitted_lag_frames=lag,fitted_lag_s=lag/30,
            fit_axes=[0,1,3,4,5],held_out_axis='elbow_flex',mae_per_joint=errors))
    args.out.mkdir(parents=True,exist_ok=True)
    result=dict(episodes=len(reports),frames=sum(r['frames'] for r in reports),motion_definition='lift/elbow changes >=2 degrees for 3 consecutive samples',
        summary=summary,episodes_detail=reports,source93_onset_frame=onset,source93_onset_s=onset/30,
        source93_first50_span=np.ptp(reference[:50],axis=0).tolist(),lag_diagnostics=lag_diagnostics,
        limitation='Fitted time shifts are diagnostic only. No leading actions or safety checks are removed.',motor_writes=0)
    (args.out/'demonstration_timing.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='episodes_detail'},ensure_ascii=False))


if __name__=='__main__':main()
