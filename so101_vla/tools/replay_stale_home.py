"""Replay a saved legacy trace without opening a camera, model or serial port."""
import argparse
import json
import math
from pathlib import Path

from legacy_motion_options import stale_home_return
from stream_live import CONFIG, interpolate, merge


def cycles(path):
    """Inference and command rows between consecutive feedback acquisitions."""
    rows = []
    with path.open('rb') as source:
        for line in source:
            prefix = line[:90]
            if b'"thermal_cycle"' in prefix:
                if rows:
                    yield rows
                rows = []
            elif any(key in prefix for key in (b'"inference"', b'"inference_requested"', b'"goal_transmitted"')):
                rows.append(json.loads(line))
    if rows:
        yield rows


def replay(path, start, end):
    home = CONFIG['home']['state']
    slots, knots, requests = {}, {}, {}
    rejected, verified_goals = [], 0
    branch = None
    for rows in cycles(path):
        if rows[0].get('time_s', 0) > end:
            break
        command = next((r for r in rows if r['event'] == 'goal_transmitted'), None)
        state = command['feedback']['state'] if command else None
        for row in rows:
            time_s = row['time_s']
            if time_s > end:
                break
            origin = row.get('origin')
            if row['event'] == 'inference_requested':
                requests[origin] = (time_s, state, row['estimated_inference_s'])
            elif row['event'] == 'inference':
                branch = None
                if row['discarded']:
                    continue
                actions, skip = row['incoming_actions'], row['skipped_actions']
                usable = origin + skip
                if time_s >= start and origin:
                    requested, observed, estimate = requests[origin]
                    if state is None or observed is None:
                        raise ValueError(f'Missing same-cycle feedback for chunk {origin}')
                    if stale_home_return(home, observed, state, actions[skip], time_s-requested):
                        # Fork immediately before installation. Playback after the old tail
                        # exhausts requires a new observation and cannot be reconstructed.
                        old_slots = {k: slots[k] for k in slots if k >= usable-1}
                        phase = command['model_phase']
                        age = time_s - requested
                        available = max(old_slots) - phase
                        rate = row['playback_hz']
                        pace = min(rate, available / (1.5 * max(age, row['seconds'], estimate * .85)))
                        incident = dict(origin=origin, delivery_s=time_s, age_s=time_s-requested,
                                        present=state, rejected=actions[skip], retained_sends=0,
                                        tail_actions=available, recovery_hz=pace)
                        rejected.append(incident)
                        branch = old_slots, incident, phase, pace / rate
                merge(slots, actions, origin, usable, raw_knots=knots)
            elif time_s >= start:
                phase = row['model_phase']
                lo, hi = math.floor(phase), math.ceil(phase)
                model = interpolate(slots[lo], slots[hi], phase-lo)
                if (knots[hi][0] != row['raw_upper_origin'] or list(knots[hi][1]) != row['raw_upper']
                        or any(abs(x-y) > 1e-7 for x, y in zip(model, row['model']))):
                    raise ValueError(f'Baseline does not reproduce logged command at {time_s}')
                verified_goals += 1
                if branch and row['raw_upper_origin'] == branch[1]['origin']:
                    previous, incident, initial_phase, scale = branch
                    # Use the recorded nominal cursor increments as the time base.
                    # Stop this fork at the next inference: new outputs are unknowable.
                    retained_phase = min(max(previous), initial_phase + (phase-initial_phase)*scale)
                    lo, hi = math.floor(retained_phase), math.ceil(retained_phase)
                    if lo in previous and hi in previous:
                        kept = interpolate(previous[lo], previous[hi], retained_phase-lo)
                        if 'retained' not in incident:
                            incident.update(first_effect_s=time_s, retained=kept)
                        incident['retained_sends'] += 1
                        if all(abs(kept[i]-home[i]) <= 25 for i in (1, 2)):
                            raise ValueError('Retained buffer also contains a home reversal')
    if not verified_goals:
        raise ValueError('Requested window contains no commands')
    return dict(window_s=[start, end], verified_baseline_commands=verified_goals, rejected=rejected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.trace, args.start, args.end), indent=2, allow_nan=False))
