"""Counterfactual target replay only: recorded states are never relabelled as new motion."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from config_runtime import load,command_limits
from gripper_safety import OpeningGuard


def closing_candidates(goals, field):
    def value(row):return row['model'] if field=='model' else row['feedback']['state']
    baseline=value(goals[0])[-1];low=baseline;peak=baseline;armed=False;candidate=None;found=[]
    for row in goals:
        v=value(row);grip=v[-1];time=row['time_s']
        if not armed:
            low=min(low,grip)
            if grip-low>=10:armed=True;peak=grip
        if armed:
            peak=max(peak,grip)
            in_pose=-32.44<=v[1]<=10.47 and -29.63<=v[2]<=29.37
            closing=in_pose and peak-grip>=max(5,.2*(peak-low))
            if closing:
                if candidate is None:candidate=dict(time_s=time,step=row['step'],pose=v)
                if time-candidate['time_s']>=.3:
                    found.append(candidate);armed=False;low=grip;candidate=None
            else:candidate=None
    visits=[]
    for row in found:
        if not visits or row['time_s']-visits[-1][-1]['time_s']>8:visits.append([])
        visits[-1].append(row)
    return dict(closures=found,visit_starts_s=[v[0]['time_s'] for v in visits],
                definition='open excursion>=10points, fall>=max(5,20%excursion), blue pose range and .3s persistence; closures <8s apart grouped',
                physical_contact_count='UNVERIFIED_NO_FULL_VIDEO')


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    goals=[]
    for line in (a.run/'steps.jsonl').open():
        r=json.loads(line)
        if r.get('event')=='goal_transmitted':goals.append(r)
    c=load();guard=OpeningGuard();low,high=c['calibration_ranges']['gripper'];max_raw=command_limits(c)['gripper'][1]
    replay=[];wire_clips=model_clips=0;peak_raw=0
    for r in goals:
        f=r['feedback'];sent=r['wire_normalized'][-1]
        limited=guard.apply(sent,f['state'][-1],f['loads']['gripper'],r['time_s'])
        raw=r['wire_raw']['gripper'] if limited==sent else min(max_raw,max(c['limits']['gripper'][0],math.floor(low+limited*(high-low)/100)))
        raw=min(raw,max_raw);peak_raw=max(peak_raw,raw)
        wire_clips+=r['wire_raw']['gripper']>max_raw;model_clips+=r['model'][-1]>c['gripper']['max_open_points']
        if r['time_s']>145 and len(replay)<15 and (not replay or r['time_s']-replay[-1]['time_s']>=.45):
            replay.append(dict(time_s=r['time_s'],old_model=r['model'][-1],old_sent=sent,old_actual=f['state'][-1],
                               new_target=(raw-low)*100/(high-low),new_raw=raw,old_load=f['loads']['gripper']))
    result=dict(source=str(a.run),source_sha256=hashlib.sha256((a.run/'steps.jsonl').read_bytes()).hexdigest(),
                steps=len(goals),cap_points=c['gripper']['max_open_points'],cap_raw=max_raw,sdk_cap_points=(max_raw-low)*100/(high-low),
                model_goals_over_cap=model_clips,original_wire_goals_over_cap=wire_clips,new_wire_peak_raw=peak_raw,
                new_wire_goals_over_cap=0,opening_guard=guard.summary(),last_seconds=replay,
                attempts_model=closing_candidates(goals,'model'),attempts_actual=closing_candidates(goals,'state'),
                physical_motor_writes=0,limitation='new target arithmetic only; original observations and overload remain original evidence, not counterfactual measured movement')
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
