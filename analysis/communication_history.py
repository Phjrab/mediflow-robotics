"""Offline temperature/transport correlation; preserve missing-data limitations."""
import argparse,collections,csv,hashlib,json,re,statistics
from pathlib import Path
JOINTS=('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper')

def audit(root,out):
    seen=set();runs=[];incidents=[];hot=[];wrist=[]
    for p in sorted(root.rglob('steps.jsonl')):
        if any(any(tag in x.lower() for tag in ('dry','mock','simulat')) for x in p.parts):continue
        digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        rows=[]
        for line in p.read_text().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        summarypath=p.parent/'summary.json'
        summary=json.loads(summarypath.read_text()) if summarypath.exists() else {}
        goals=[r for r in rows if r.get('event')=='goal_transmitted' and 'feedback' in r]
        previous=None;motion=[];last_feedback=None;last_cycle=None;latest_pose=None
        for number,r in enumerate(rows,1):
            event=r.get('event')
            if event=='goal_transmitted' and 'feedback' in r:
                state=r['feedback'].get('state');t=r.get('time_s')
                if state and previous and t is not None and previous[0] is not None and t>previous[0]:
                    speed={j:(state[i]-previous[1][i])/(t-previous[0]) for i,j in enumerate(JOINTS)}
                    motion.append(dict(t=t,state=state,speed=speed));wrist.append(dict(run=str(p.parent),**motion[-1]))
                if state:previous=(t,state);latest_pose=state
                last_feedback=r['feedback']
            if event=='thermal_cycle':
                last_cycle=r
                temps=r.get('temperature_raw_c',{});suspected=[j for j,v in temps.items() if v>=55 or r.get('diagnostics',{}).get(j,{}).get('suspected_spike')]
                for joint in suspected:
                    hot.append(dict(run=str(p.parent),line=number,monotonic_s=r.get('monotonic_s'),joint=joint,
                        raw_c=temps[joint],voltage_v=r.get('voltage_v',{}).get(joint),
                        simultaneous_hot_axes=suspected,cached_pose=latest_pose,
                        cached_wrist_velocity_deg_s=None if not motion else {j:motion[-1]['speed'][j] for j in ('wrist_flex','wrist_roll')},
                        same_payload_hex=r.get('feedback_bytes_hex',{}).get(joint)))
        texts=[]
        if summary.get('reason'):texts.append(summary['reason'])
        for r in rows:
            if r.get('event') in ('thermal_read_error','stop','error'):
                texts.append(json.dumps(r,ensure_ascii=False))
        pairs=set()
        for text in texts:
            match=re.search(r'id[=:]\s*(\d+).*?통신 코드[=:]\s*(-[67])',text)
            if match:pairs.add((int(match[1]),int(match[2])))
            for m in re.finditer(r'"expected_id"\s*:\s*(\d+).*?"communication"\s*:\s*(-[67])',text):pairs.add((int(m[1]),int(m[2])))
        for mid,code in sorted(pairs):
            incidents.append(dict(run=str(p.parent),joint=JOINTS[mid-1],motor_id=mid,communication=code,
                elapsed_s=summary.get('elapsed_s'),goal_steps=summary.get('steps'),
                cached_pose=None if last_feedback is None else last_feedback.get('state'),
                last_valid_temperatures=None if last_cycle is None else last_cycle.get('temperature_raw_c'),
                last_valid_voltages=None if last_cycle is None else last_cycle.get('voltage_v'),
                cached_wrist_velocity_deg_s=None if not motion else {j:motion[-1]['speed'][j] for j in ('wrist_flex','wrist_roll')},
                simultaneous_other_axis_errors='NOT_CAPTURED_AT_FAILED_TRANSACTION',
                raw_failed_packet='NOT_CAPTURED_IN_OLD_SUMMARY',
                occurrence_count='one incident per run/code/axis; retries not recorded in old logs'))
        if goals:runs.append(dict(run=str(p.parent),sha256=digest,steps=len(goals),summary_steps=summary.get('steps'),
                                 last_model_time_s=goals[-1].get('playback_s')))
    def q(vals):
        vals=sorted(vals)
        return dict(p50=statistics.median(vals),p95=vals[min(len(vals)-1,int(.95*len(vals)))],max=max(vals)) if vals else None
    result=dict(unique_physical_step_files=len(seen),runs=runs,communication_incidents=incidents,
        incident_counts_by_axis={j:dict(collections.Counter(str(x['communication']) for x in incidents if x['joint']==j)) for j in JOINTS},
        temperature_hot_or_suspected_rows=len(hot),hot_counts_by_axis=dict(collections.Counter(x['joint'] for x in hot)),
        multiple_hot_axes_same_bulk_cycle=sum(len(x['simultaneous_hot_axes'])>1 for x in hot),
        wrist_motion_all_steps={j:q([abs(x['speed'][j]) for x in wrist]) for j in ('wrist_flex','wrist_roll')},
        limitations=['No failed raw packets or request-level timestamps in older logs: exact cross-axis simultaneity cannot be reconstructed.',
            'Cached pose/speed precedes event and is not simultaneous. Counts are deduplicated run incidents, not wire error totals.',
            'Temperature bulk measurements may be corrupt despite SDK status success. Voltage in same payload is not an independent electrical measurement.',
            'Sparse communication incidents cannot establish cable motion causality.'],
        bus_budget=dict(old_per_cycle_packets=27,deployed_per_cycle_packets=27,
            old_per_cycle_bytes=354,deployed_per_cycle_bytes=354,
            rejected_combined_trial=dict(packets_per_cycle=20,bytes_per_cycle=394),
            note='Includes six replies, six fresh individual temperatures and one goal. Wide READ33..70 trial had more corrupt replies; deployed runner retains narrow READ56:15 plus READ33:8.',
            at_50hz=dict(old_packets_s=1350,deployed_packets_s=1350,old_line_occupancy_percent=17.7,deployed_line_occupancy_percent=17.7),
            baud=1000000,bits_per_byte=10))
    (out/'communication_analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (out/'temperature_correlated.json').write_text(json.dumps(hot,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('runs','communication_incidents')},ensure_ascii=False))
    print(json.dumps(incidents,ensure_ascii=False))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(exist_ok=True,parents=True);audit(a.root,a.out)
