"""Read-only size and consumed-chunk audit of historical stream JSONL logs.

No robot/model imports. The historical logs lack complete incoming chunks, so
movement is explicitly the *applied, interpolated/blended* model trajectory.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics

JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def stats(values):
    values = sorted(values)
    if not values:
        return dict(count=0, mean=None, p50=None, min=None, max=None)
    return dict(count=len(values), mean=statistics.mean(values), p50=statistics.median(values),
                min=values[0], max=values[-1])


def trajectory(rows, key):
    vectors = [row[key] for row in rows if isinstance(row.get(key), (list,tuple)) and len(row[key])==len(JOINTS)]
    if not vectors:
        return {}
    return {joint: dict(span=max(v[i] for v in vectors)-min(v[i] for v in vectors),
                        delta=vectors[-1][i]-vectors[0][i],
                        path_length=sum(abs(b[i]-a[i]) for a,b in zip(vectors,vectors[1:])))
            for i, joint in enumerate(JOINTS)}


def _image_bytes(value, path=()):
    """Count encoded image payload values only, not ordinary frame metadata."""
    if isinstance(value, dict):
        return sum(_image_bytes(v, (*path,k)) for k,v in value.items())
    if path and path[-1] in ('pixels', 'jpeg_base64', 'jpg_base64', 'image_base64', 'jpeg', 'jpg'):
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    return 0


def lines(path):
    with path.open('rb') as source:
        yield from source


def analyze(run, window=60.):
    run = Path(run)
    trace = run/'steps.jsonl' if run.is_dir() else run
    counts, sizes, field_sizes = Counter(), Counter(), defaultdict(Counter)
    compact_bytes = 0
    image_payload_bytes = 0
    goals, inferences = [], []
    last_wire = None
    loop_origins, unix_origins = [], []
    first_wire_unix = None
    current_inference = None
    malformed = []
    for line_no, raw in enumerate(lines(trace), 1):
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            malformed.append(dict(line=line_no, error=str(exc)))
            continue
        event = row.get('event', '(no_event)')
        counts[event] += 1
        sizes[event] += len(raw)
        compact_bytes += len(json.dumps(row, ensure_ascii=False, separators=(',',':')).encode('utf-8'))+1
        for key, value in row.items():
            field_sizes[event][key] += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        image_payload_bytes += _image_bytes(row)
        if event in ('wire_tx','wire_rx'):
            last_wire = row
            if first_wire_unix is None:
                first_wire_unix = row.get('unix')
        elif event == 'inference':
            current_inference = len(inferences)
            inferences.append(dict(row, _wire=last_wire, _index=current_inference))
        elif event == 'goal_transmitted':
            if last_wire and 'monotonic_s' in last_wire:
                loop_origins.append(last_wire['monotonic_s']-row['time_s'])
                if last_wire.get('unix') is not None:
                    unix_origins.append(last_wire['unix']-row['time_s'])
            # Retain only relevant parts of the large feedback record.
            goals.append(dict(time_s=row['time_s'], phase=row.get('model_phase'),
                model=row.get('model'), sent=row.get('wire_normalized'),
                state=row.get('feedback',{}).get('state'), inference=current_inference))
    mono_origin = statistics.median(loop_origins) if loop_origins else None
    unix_origin = statistics.median(unix_origins) if unix_origins else None
    for item in inferences:
        wire = item.pop('_wire')
        item['delivery_time_s_approx'] = (wire['monotonic_s']-mono_origin
            if wire and mono_origin is not None else None)
    segments = []
    for inference in inferences:
        rows = [g for g in goals if g['inference']==inference['_index'] and g['time_s']<=window]
        if not rows:
            continue
        entry = {k:v for k,v in inference.items() if k!='_index'}
        phase0, phase1 = rows[0]['phase'], rows[-1]['phase']
        entry.update(inference_number=inference['_index']+1, first_goal_s=rows[0]['time_s'],
            last_goal_s=rows[-1]['time_s'], wire_commands=len(rows),
            consumed_phase_span=phase1-phase0,
            distinct_model_knots_touched=len(set(int(g['phase']) for g in rows)),
            first_phase=phase0,last_phase=phase1,
            applied_model_trajectory=trajectory(rows,'model'),
            actual_trajectory=trajectory(rows,'state'))
        segments.append(entry)
    early_inf = [i for i in inferences if i['delivery_time_s_approx'] is not None and i['delivery_time_s_approx']<=window]
    origins = [i['origin'] for i in early_inf if not i.get('discarded')]
    deliveries = [i['delivery_time_s_approx'] for i in early_inf]
    early_goals = [g for g in goals if g['time_s']<=window]
    full_segments = [s for s in segments if s['inference_number'] < (early_inf[-1]['_index']+1 if early_inf else 0)]
    summary_path = trace.parent/'summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}
    total_bytes = sum(sizes.values())
    sizes_report = [dict(event=e, bytes=b, mb=b/1e6, percent=100*b/total_bytes,
        records=counts[e], top_value_fields=dict(field_sizes[e].most_common(8))) for e,b in sizes.most_common()]
    return dict(run=str(trace.parent), window_s=window, first_goal_s=goals[0]['time_s'] if goals else None,
        first_wire_unix=first_wire_unix, loop_start_unix_approx=unix_origin,
        preparation_before_loop_s=unix_origin-first_wire_unix if unix_origin and first_wire_unix else None,
        first60=dict(inference_count=len(early_inf), inference_seconds=stats([i['seconds'] for i in early_inf]),
            delivery_intervals_s=stats([b-a for a,b in zip(deliveries,deliveries[1:])]),
            request_origin_advance_actions=stats([b-a for a,b in zip(origins,origins[1:])]),
            consumed_phase_per_complete_delivery_segment=stats([s['consumed_phase_span'] for s in full_segments]),
            skipped_actions=stats([i['skipped_actions'] for i in early_inf]),
            overlap_actions=stats([i['overlap'] for i in early_inf]),
            applied_model_trajectory=trajectory(early_goals,'model'),
            sent_trajectory=trajectory(early_goals,'sent'), actual_trajectory=trajectory(early_goals,'state'),
            wire_commands=len(early_goals), chunks=segments),
        log=dict(bytes=total_bytes, compact_json_bytes=compact_bytes,
                 whitespace_saving_bytes=total_bytes-compact_bytes,
                 image_payload_value_bytes=image_payload_bytes, categories=sizes_report, malformed=malformed),
        run_summary={key:summary.get(key) for key in ('status','elapsed_s','speed','playback_hz','playback_s',
                        'inference_s','chunk_wait_s','transmission_hz','steps')},
        limitations=['Delivery time estimated from immediately preceding serial trace; sub-loop uncertainty.',
            'Complete unblended incoming model chunks were not logged. Applied trajectory spans are not raw chunk spans.',
            'Delivery segments include linear overlap with predecessor; counts are timeline consumed, not exclusive chunk attribution.',
            'Recorded images/real-world scene differ between runs; scheduling correlation does not establish perception causality.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--window',type=float,default=60.)
    args=parser.parse_args()
    result=dict(runs=[analyze(run,args.window) for run in args.runs],motor_access=0,motor_writes=0)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in result['runs']:
        print(json.dumps(dict(run=row['run'],first60={k:v for k,v in row['first60'].items() if k!='chunks'},
              log={k:v for k,v in row['log'].items() if k!='categories'},
              largest_log_categories=[{k:v for k,v in e.items() if k!='top_value_fields'} for e in row['log']['categories'][:6]]),ensure_ascii=False))


if __name__=='__main__':main()
