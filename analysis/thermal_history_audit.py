"""Aggregate preserved physical run/read logs; no devices and no original edits."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')


def sign(value,bit):
    return -(value-(1<<bit)) if value & (1<<bit) else value


def audit(root,out):
    counts={j:dict(samples=0,raw_ge55_samples=0,abrupt_suspect_samples=0,peak_c=0) for j in JOINTS}
    evidence,files,seen=[],[],set()
    for path in sorted([*root.rglob('steps.jsonl'),*root.rglob('readings.jsonl')]):
        if any(x.startswith(('DRY_','dry_','mock_','sim_')) for x in path.parts):
            continue
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        files.append(dict(path=str(path),sha256=digest))
        has_cycles=False;last_torque={};previous={};histories=collections.defaultdict(list)
        for index,line in enumerate(raw.decode('utf-8').splitlines(),1):
            try:row=json.loads(line)
            except (ValueError,TypeError):continue
            event=row.get('event')
            if event=='startup_ready':last_torque=dict(row.get('torque',{}))
            mappings=[]
            if event=='thermal_cycle':
                has_cycles=True
                for j,t in row.get('temperature_raw_c',{}).items():
                    if j not in counts:continue
                    payload=row.get('feedback_bytes_hex',{}).get(j)
                    load=current=None
                    if payload:
                        block=bytes.fromhex(payload)
                        load=sign(int.from_bytes(block[4:6],'little'),10)
                        current=sign(int.from_bytes(block[13:15],'little'),15)
                    mappings.append((j,t,row.get('monotonic_s'),row.get('voltage_v',{}).get(j),load,current,
                                     last_torque.get(j),'startup_readback' if j in last_torque else 'NOT_RECORDED',
                                     row.get('diagnostics',{}).get(j,{}).get('suspected_spike',False),payload))
            elif event=='thermal_readonly_sample':
                j=row['joint'];same=row.get('same_packet',{})
                mappings=[(j,row['temperature_raw_c'],row.get('monotonic_s'),row.get('voltage_v'),
                    sign(same.get('load_register_raw',0),10),sign(same.get('current_register_raw',0),15),
                    row.get('torque'),'same_read_cycle',row.get('diagnostics',{}).get('suspected_spike',False),same.get('payload_hex'))]
            elif not has_cycles and event in ('goal_transmitted','stop_feedback'):
                fb=row.get('feedback',row)
                for j,t in fb.get('temperatures',{}).items():
                    if j in counts:mappings.append((j,t,row.get('time_s'),fb.get('voltage_v',{}).get(j),
                        fb.get('loads',{}).get(j),fb.get('current_raw',{}).get(j),fb.get('torque',last_torque).get(j),
                        'feedback_or_startup' if j in fb.get('torque',last_torque) else 'NOT_RECORDED',False,None))
            for j,t,now,voltage,load,current,torque,basis,suspect,payload in mappings:
                c=counts[j];c['samples']+=1;c['raw_ge55_samples']+=int(t>=55);c['peak_c']=max(c['peak_c'],t)
                if j in previous and t-previous[j]>10:suspect=True
                if now is not None:
                    history=histories[j]
                    history[:]=[(s,v) for s,v in history if 0<=now-s<=1]
                    if history and t-min(v for _,v in history)>5:suspect=True
                    history.append((now,t))
                previous[j]=t;c['abrupt_suspect_samples']+=int(suspect)
                if suspect or t>=55:
                    evidence.append(dict(source=str(path),line=index,event=event,joint=j,temperature_c=t,
                        abrupt_suspect=bool(suspect),raw_ge55=t>=55,voltage_v=voltage,load_raw=load,
                        current_raw=current,torque=torque,torque_basis=basis,payload_hex=payload))
    result=dict(scope='unique steps.jsonl and readings.jsonl under so101_vla; DRY/mock/sim excluded',
        unique_files=len(files),joints=counts,source_files=files,
        hot_values_are_not_proof_of_physical_overheat=True,
        torque_note='Old logs without a torque read are NOT_RECORDED; startup_readback is not simultaneous with temperature.')
    (out/'history_counts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if evidence:
        with (out/'suspect_samples.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(evidence[0]));writer.writeheader();writer.writerows(evidence)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True);d=audit(a.root,a.out)
    print(json.dumps({k:v for k,v in d.items() if k!='source_files'},ensure_ascii=False))
