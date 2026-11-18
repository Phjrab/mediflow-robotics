"""Audit a completed live run and read-only dashboard snapshots; never opens devices."""
import argparse
import base64
import collections
import csv
import hashlib
import json
import statistics
import time
import urllib.request
from pathlib import Path

from config_runtime import load
from thermal_window import ThermalWindow

JOINTS=load()['joint_names']

def limits(values):
    return {j:dict(min=min(row),max=max(row),span=max(row)-min(row)) for j,row in zip(JOINTS,zip(*values))} if values else {}

def section(goals):
    return dict(steps=len(goals),model=limits([r['model'] for r in goals]),
        sent=limits([r['sent'] for r in goals]),observed=limits([r['state'] for r in goals]),
        elbow_same_frame_error_deg=dict(mean=statistics.mean(abs(r['sent'][2]-r['state'][2]) for r in goals),
            max=max(abs(r['sent'][2]-r['state'][2]) for r in goals)))

def review(run,out,previous,reference):
    out.mkdir(exist_ok=False,parents=True)
    summary=json.loads((run/'summary.json').read_text())
    (out/'summary_copy.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    events=collections.Counter();goals=[];inferences=[];hold_failures=[];transport_errors=[]
    rx=collections.Counter();tx=collections.Counter();rx_invalid=[];thermal_mismatches=[]
    thermal=ThermalWindow(JOINTS,load()['temperature']);cycles=0;thermal_holds=0;temp_intervals=[]
    comp=collections.Counter();comp_peak=0.;comp_nonzero=0
    digest=hashlib.sha256()
    with (out/'elbow_steps.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['step','time_s','playback_s','model_deg','sent_deg','actual_deg',
            'goal_ticks','actual_ticks','load_raw','current_raw','compensation_deg','compensation_reason'])
        writer.writeheader()
        with (run/'steps.jsonl').open('rb') as source:
            for line in source:
                digest.update(line);r=json.loads(line);e=r.get('event');events[e]+=1
                if e=='goal_transmitted':
                    fb=r['feedback'];c=r.get('tracking_compensation',{})
                    offset=c.get('offset_deg',[0.]*6)[2];comp_peak=max(comp_peak,abs(offset));comp_nonzero+=abs(offset)>1e-9
                    reason=c.get('reason',['unknown']*6)[2];comp[reason]+=1
                    g=dict(step=r['step'],time_s=r['time_s'],playback_s=r['playback_s'],model=r['model'],
                           sent=r['wire_normalized'],state=fb['state'])
                    goals.append(g)
                    writer.writerow(dict(step=r['step'],time_s=r['time_s'],playback_s=r['playback_s'],model_deg=r['model'][2],
                        sent_deg=r['wire_normalized'][2],actual_deg=fb['state'][2],goal_ticks=r['wire_raw']['elbow_flex'],
                        actual_ticks=fb['present_raw']['elbow_flex'],load_raw=fb['loads']['elbow_flex'],
                        current_raw=fb.get('current_raw',{}).get('elbow_flex'),compensation_deg=offset,compensation_reason=reason))
                elif e=='inference':inferences.append(r)
                elif e=='hold_attempt_failed':hold_failures.append(r)
                elif e in ('communication_error','communication_cycle_deferred','rx_resynchronization','thermal_read_error'):
                    transport_errors.append(r)
                elif e=='wire_rx':
                    rx[str(r.get('communication'))]+=1
                    if r.get('communication')!=0 or not r.get('checksum_valid'):rx_invalid.append(r)
                elif e=='wire_tx':tx[(r.get('instruction'),r.get('address'))]+=1
                elif e=='thermal_cycle':
                    cycles+=1
                    for trace in r['reply_trace']:
                        if 'value_c' not in trace:continue
                        j=trace['joint'];decision=thermal.observe_axis(j,trace['observed_monotonic_s'],trace['value_c'])
                        reported=r['temperature_protection'][j]
                        if any(decision[k]!=reported[k] for k in ('hold','warning','median_c','continuous_hot_s','spike_count')):
                            thermal_mismatches.append(dict(cycle=cycles,joint=j,actual=decision,reported=reported))
                        thermal_holds+=decision['hold']
                        if trace.get('request_interval_s') is not None:temp_intervals.append(trace['request_interval_s'])
    prior=json.loads((previous/'summary.json').read_text());prior_goals=[]
    for line in (previous/'steps.jsonl').open():
        r=json.loads(line)
        if r.get('event')=='goal_transmitted':prior_goals.append(dict(step=r['step'],time_s=r['time_s'],playback_s=r['playback_s'],
            model=r['model'],sent=r['wire_normalized'],state=r['feedback']['state']))
    prior_window=prior_goals[-1]['playback_s'];common=[r for r in goals if r['playback_s']<=prior_window]
    training=json.loads(reference.read_text())
    nearest=[]
    for c in training['candidates']:
        best=min(goals,key=lambda r:sum((a-b)**2 for a,b in zip(r['model'],c['action'])))
        nearest.append(dict(episode=c['merged_episode'],candidate_time_s=c['closing_s'],
            live_step=best['step'],model_difference=[a-b for a,b in zip(best['model'],c['action'])]))
    result=dict(run=str(run),summary=summary,events=dict(events),source_sha256=dict(steps=digest.hexdigest(),
        summary=hashlib.sha256((run/'summary.json').read_bytes()).hexdigest()),
        transport=dict(rx_code_counts=dict(rx),invalid_rx_count=len(rx_invalid),invalid_rx=rx_invalid[:10],
            error_events=transport_errors,hold_failures=hold_failures,
            tx_by_instruction_address={f'{a}:{b}':n for (a,b),n in tx.items()}),
        thermal=dict(cycles=cycles,replayed_holds=thermal_holds,decision_mismatches=thermal_mismatches,
            replay_summary=thermal.summary(),individual_interval_min_s=min(temp_intervals,default=None),
            individual_interval_max_s=max(temp_intervals,default=None)),
        motion=section(goals),elbow_compensation=dict(nonzero_steps=comp_nonzero,max_abs_deg=comp_peak,reasons=dict(comp)),
        comparison=dict(previous_run=str(previous),previous_summary_tracking=prior.get('tracking'),
            common_playback_s=prior_window,previous=section(prior_goals),current_common_window=section(common)),
        training=dict(episodes=training['training_episodes'],closing_candidates=training['closing_candidates'],
            candidate_time_s=training['candidate_time_s'],action_at_closure=training['training_grasp_action'],
            recorded_model_note='model field is interpolated/merged action before compensation, not raw unmerged network chunks',
            nearest_candidate_comparisons=nearest),
        inference=dict(count=len(inferences),discarded=sum(bool(x.get('discarded')) for x in inferences),
            overlap_max_delta={j:max((r['blend_max_delta'][i] for r in inferences),default=0) for i,j in enumerate(JOINTS)},
            skip_min=min((x['skipped_actions'] for x in inferences),default=None),
            skip_max=max((x['skipped_actions'] for x in inferences),default=None)))
    try:
        address='http://100.64.203.77:8766'
        status=json.load(urllib.request.urlopen(address+'/api/status',timeout=5));status.pop('control_token',None)
        observation=json.load(urllib.request.urlopen(address+'/api/observation',timeout=5))
        (out/'monitor_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
        from PIL import Image
        metadata={k:v for k,v in observation.items() if k!='images'};metadata['images']={}
        for role,data in observation.get('images',{}).items():
            if role not in ('end_effector','ceiling_vertical','ceiling_oblique'):continue
            path=out/(role+'_last.jpg');raw=base64.b64decode(data,validate=True);path.write_bytes(raw)
            with Image.open(path) as im:width,height=im.size
            metadata['images'][role]=dict(width=width,height=height,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        (out/'observation_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
        result['monitor']=dict(status_run=status.get('run'),phase=status.get('phase'),step=status.get('step'),
            observation_run=metadata.get('run'),capture=metadata.get('capture'),captured=metadata.get('captured'),
            status_updated=status.get('updated'),images=metadata['images'],retrieved_unix=time.time())
    except Exception as e:result['monitor_error']=str(e)
    (out/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('summary','training')},ensure_ascii=False))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    a=p.parse_args();review(a.run,a.out,a.previous,a.reference)
