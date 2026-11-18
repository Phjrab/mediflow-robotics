"""Replay only the pan-offset arithmetic on an immutable previous run."""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
from config_runtime import load,command_limits
from grasp_offset import GraspOffset


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();source=args.run/'steps.jsonl'
    rows=[r for line in source.open() if (r:=json.loads(line)).get('event')=='goal_transmitted']
    times=[r['time_s'] for r in rows];guard=GraspOffset();config=load();lo,hi=command_limits()['shoulder_pan']
    changed=[];outside=[];raw_peak=0
    def raw(angle):return min(hi,max(lo,int(sum(config['calibration_ranges']['shoulder_pan'])/2+angle*4095/360)))
    for index,row in enumerate(rows):
        future=[]
        for step in range(1,11):
            i=bisect.bisect_left(times,row['time_s']+step/9.9)
            if i>=len(rows):break
            future.append(rows[i]['model'])
        target,detail=guard.apply(row['model'],row['feedback']['state'],row['time_s'],future,9.9)
        if target[1:]!=row['model'][1:]:raise AssertionError('non-pan lateral correction')
        if detail['applied_deg']:
            difference=raw(target[0])-raw(row['model'][0]);raw_peak=max(raw_peak,abs(difference))
            changed.append(dict(time_s=row['time_s'],step=row['step'],applied_deg=detail['applied_deg'],raw_delta_ticks=difference,nominal_height_mm=detail['nominal_height_mm']))
            if not detail['eligible'] or abs(detail['applied_deg'])>config['lateral_correction']['max_abs_offset_deg']:outside.append(row['step'])
    result=dict(source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),steps=len(rows),
                summary=guard.summary(),peak_integer_pan_offset=raw_peak,outside_gate=outside,changed_examples=changed[:8]+changed[-8:],
                first_change_s=changed[0]['time_s'] if changed else None,last_change_s=changed[-1]['time_s'] if changed else None,
                physical_motor_writes=0,limitation='recorded future goals sampled as a proxy for runtime queue; counterfactual arithmetic, not actual movement')
    assert changed and not outside and raw_peak<=7
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
