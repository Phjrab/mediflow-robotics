"""Compare blue training closing candidates to model, sent and observed poses."""
import argparse,csv,json,collections,statistics
from pathlib import Path

JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')

def analyze(root,out):
    import numpy as np
    import pyarrow.parquet as pq
    mapping=root/'_archive/20261110/jetson/home/motion_limits_20261109_1794202196649/episode_mapping.csv'
    episodes=[x for x in csv.DictReader(mapping.open()) if x['color']=='blue']
    by_source=collections.defaultdict(list)
    for x in episodes:by_source[x['source_repo_id']].append(x)
    candidates=[];previews=[]
    for source,selected in by_source.items():
        data_root=Path.home()/'.cache/huggingface/lerobot'/source
        selected_ids={int(x['source_episode_index']) for x in selected};by_episode=collections.defaultdict(list)
        for p in (data_root/'data').rglob('*.parquet'):
            for row in pq.read_table(p,columns=['episode_index','frame_index','timestamp','action','observation.state']).to_pylist():
                if row['episode_index'] in selected_ids:by_episode[row['episode_index']].append(row)
        for meta in selected:
            number=int(meta['source_episode_index']);rows=sorted(by_episode[number],key=lambda x:x['frame_index'])
            grip=[x['action'][5] for x in rows];baseline=grip[0]
            opening=next((i for i,g in enumerate(grip) if g>baseline+10),None)
            if opening is None:continue
            peak=grip[opening];closing=None
            for i in range(opening+1,len(rows)-3):
                peak=max(peak,grip[i])
                threshold=max(5.,.2*(peak-baseline))
                if (i-opening>=12 and all(grip[k]<peak-threshold for k in range(i,i+3))
                        and rows[i]['action'][2]<70 and rows[i]['action'][1]>-80):closing=i;break
            if closing is None:continue
            row=rows[closing]
            candidates.append(dict(merged_episode=int(meta['merged_episode_index']),source=source,source_episode=number,
                opening_frame=opening,closing_frame=closing,closing_s=closing/30,
                action=row['action'],state=row['observation.state']))
            if source.endswith('a_to_blue_20261103_193835') and len(previews)<3:
                from eval_blue import frames_for,CAMERAS
                info=json.loads((data_root/'meta/info.json').read_text());metadata=[e for p in (data_root/'meta/episodes').rglob('*.parquet') for e in pq.read_table(p).to_pylist() if e['episode_index']==number][0]
                offsets=sorted(set([max(0,closing-10),closing,min(len(rows)-1,closing+15)]))
                frames=frames_for(data_root,info,metadata,rows,offsets)
                from PIL import Image
                for offset in offsets:
                    Image.fromarray(frames[offset]['ceiling_oblique']).save(out/f'training_e{number}_f{offset}_oblique.jpg')
                previews.append(dict(source=source,episode=number,closing_frame=closing,offsets=offsets))
    run=root/'runs/20261115_181130';records=[json.loads(x) for x in (run/'steps.jsonl').read_text().splitlines()];steps=[x for x in records if x.get('event')=='goal_transmitted']
    def ranges(values):
        a=np.array(values,dtype=float)
        return {j:dict(min=float(a[:,i].min()),p50=float(np.median(a[:,i])),max=float(a[:,i].max()),p5=float(np.percentile(a[:,i],5)),p95=float(np.percentile(a[:,i],95))) for i,j in enumerate(JOINTS)}
    result=dict(training_episodes=44,closing_candidates=len(candidates),method='first sustained >=max(5points,20%opening excursion) closure after opening >=10points, elbow<70deg and lift>-80deg; candidate pending visual review',
        candidate_time_s=dict(min=min(x['closing_s'] for x in candidates),p50=statistics.median(x['closing_s'] for x in candidates),max=max(x['closing_s'] for x in candidates)),
        training_grasp_action=ranges([x['action'] for x in candidates]),training_grasp_state=ranges([x['state'] for x in candidates]),
        live_model=ranges([x['model'] for x in steps]),live_sent=ranges([x['wire_normalized'] for x in steps]),live_state=ranges([x['feedback']['state'] for x in steps]),
        model_timeline_s=steps[-1]['playback_s'],candidate_reached_by_time=sum(x['closing_s']<=steps[-1]['playback_s'] for x in candidates),previews=previews,candidates=candidates)
    (out/'grasp_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('candidates','previews')},ensure_ascii=False))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(exist_ok=True,parents=True);analyze(a.root,a.out)
