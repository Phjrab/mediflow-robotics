"""Compare the recorded 66C/81C events without opening hardware."""
import argparse
import csv
import json
from pathlib import Path


OLDER = '20261109_215013_HOLD_temperature_296steps_vla_stream_blue_1794228613187797901'


def signed(value, bit):
    return -(value-(1 << bit)) if value & (1 << bit) else value


def block_values(raw_hex):
    raw = bytes.fromhex(raw_hex)
    word = lambda i: int.from_bytes(raw[i:i+2], 'little')
    return dict(position_ticks=signed(word(0), 15), speed_raw=signed(word(2), 15),
                load_raw=signed(word(4), 10), voltage_v=raw[6]/10, temperature_c=raw[7],
                async_write_flag=raw[8], status=raw[9], moving=raw[10],
                active_goal_ticks=word(11), current_raw=signed(word(13), 15), raw_hex=raw_hex)


def save_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['no_samples'])
        writer.writeheader()
        writer.writerows(rows)


def analyze(root, out):
    new = root/'runs/20261115_162129'
    rows = [json.loads(s) for s in (new/'steps.jsonl').read_text().splitlines()]
    cycles = [x for x in rows if x.get('event') == 'thermal_cycle']
    trigger = next(x for x in cycles if x['temperature_raw_c']['shoulder_lift'] >= 55)
    t0 = trigger['monotonic_s']
    window = []
    for x in cycles:
        delta = x['monotonic_s']-t0
        if abs(delta) <= .5:
            values = block_values(x['feedback_bytes_hex']['shoulder_lift'])
            window.append(dict(relative_s=delta, sequence=x['sequence'],
                               temperature_individual_max_c=x['temperature_raw_c']['shoulder_lift'],
                               errors_total=x['error_counts']['total'], **values))
    save_csv(out/'shoulder_lift_66C_window.csv', window)
    steps = [x for x in rows if x.get('event') == 'goal_transmitted']
    series = [dict(step=x['step'], time_s=x['time_s'], model_phase=x['model_phase'],
                   model_deg=x['model'][2], sent_deg=x['wire_normalized'][2],
                   sent_ticks=x['wire_raw']['elbow_flex'], actual_deg=x['feedback']['state'][2],
                   actual_ticks=x['feedback']['present_raw']['elbow_flex'],
                   load_raw=x['feedback']['loads']['elbow_flex'],
                   current_raw=x['feedback']['current_raw']['elbow_flex'],
                   gap_ticks=x['wire_raw']['elbow_flex']-x['feedback']['present_raw']['elbow_flex'],
                   elbow_clipped='elbow_flex' in x.get('clipped_joints', [])) for x in steps]
    save_csv(out/'elbow_209_steps.csv', series)
    stops = []
    begin = 0
    for i in range(1, len(series)+1):
        if i == len(series) or series[i]['actual_ticks'] != series[begin]['actual_ticks']:
            elapsed = series[i-1]['time_s']-series[begin]['time_s']
            if elapsed >= .1:
                stops.append(dict(start_step=series[begin]['step'], end_step=series[i-1]['step'],
                                  duration_s=elapsed, actual_ticks=series[begin]['actual_ticks'],
                                  sent_ticks_min=min(x['sent_ticks'] for x in series[begin:i]),
                                  sent_ticks_max=max(x['sent_ticks'] for x in series[begin:i])))
            begin = i
    older = root/'runs'/OLDER
    old_summary = json.loads((older/'summary.json').read_text())
    old_rows = [json.loads(s) for s in (older/'steps.jsonl').read_text().splitlines()]
    old_steps = [x for x in old_rows if x.get('event') == 'goal_transmitted']
    old_stop = next(x for x in old_rows if x.get('event') == 'stop_feedback')
    old_end = old_steps[-1]['time_s']
    old_window = []
    for x in old_steps:
        if old_end-x['time_s'] <= .5:
            feedback = x['feedback']
            old_window.append(dict(relative_to_last_goal_s=x['time_s']-old_end,
                position_ticks=feedback['present_raw']['shoulder_lift'],
                temperature_c=feedback['temperatures']['shoulder_lift'],
                load_raw=feedback['loads']['shoulder_lift'],
                current_raw=feedback['current_raw']['shoulder_lift'],
                status=feedback['statuses']['shoulder_lift'], voltage_v='NOT_RECORDED'))
    old_window.append(dict(relative_to_last_goal_s='STOP_AFTER_LAST_GOAL',
        position_ticks=old_stop['present_raw']['shoulder_lift'],
        temperature_c=old_stop['temperatures']['shoulder_lift'],
        load_raw=old_stop['loads']['shoulder_lift'], current_raw=old_stop['current_raw']['shoulder_lift'],
        status=old_stop['statuses']['shoulder_lift'], voltage_v='NOT_RECORDED'))
    save_csv(out/'shoulder_lift_81C_window.csv', old_window)
    new_stop = next(x for x in rows if x.get('event') == 'stop_feedback')
    def compare(steps, stop):
        loads = [abs(x['feedback']['loads']['shoulder_lift']) for x in steps]
        return dict(start_deg=steps[0]['feedback']['state'][1], final_deg=stop['state'][1],
                    delta_deg=stop['state'][1]-steps[0]['feedback']['state'][1],
                    stop_load_raw=stop['loads']['shoulder_lift'], max_abs_load_during_motion=max(loads),
                    stop_current_raw=stop['current_raw']['shoulder_lift'],
                    max_abs_current_during_motion=max(abs(x['feedback']['current_raw']['shoulder_lift']) for x in steps))
    result = dict(new_run=str(new), older_run=str(older),
        window_66=dict(samples=len(window), first_relative_s=window[0]['relative_s'], last_relative_s=window[-1]['relative_s'],
            voltage_min_v=min(x['voltage_v'] for x in window), voltage_max_v=max(x['voltage_v'] for x in window),
            load_min_raw=min(x['load_raw'] for x in window), load_max_raw=max(x['load_raw'] for x in window),
            current_min_raw=min(x['current_raw'] for x in window), current_max_raw=max(x['current_raw'] for x in window),
            detected_errors=max(x['errors_total'] for x in window),
            at_trigger=next(x for x in window if x['relative_s']==0)),
        lift_comparison=dict(new_66=compare(steps, new_stop), older_81=compare(old_steps, old_stop),
                             older_voltage_and_raw_packet='NOT_RECORDED', older_after_stop_samples='NOT_RECORDED'),
        elbow=dict(steps=len(series), clipped_total=sum(x['elbow_clipped'] for x in series),
            wire_at_3077=sum(x['sent_ticks']==3077 for x in series), wire_at_actual_upper_3082=sum(x['sent_ticks']==3082 for x in series),
            observed_unchanged_intervals=stops, first=series[0], last=series[-1],
            sent_ticks_min=min(x['sent_ticks'] for x in series), sent_ticks_max=max(x['sent_ticks'] for x in series),
            actual_ticks_min=min(x['actual_ticks'] for x in series), actual_ticks_max=max(x['actual_ticks'] for x in series)))
    (out/'log_analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(exist_ok=True, parents=True)
    print(json.dumps(analyze(a.root.expanduser(), a.out.expanduser()), ensure_ascii=False))
