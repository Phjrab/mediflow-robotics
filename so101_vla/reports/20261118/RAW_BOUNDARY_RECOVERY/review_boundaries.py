"""Read saved dry-run traces only; no serial, cameras, or inference."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('run', type=Path)
a = p.parse_args()
run = a.run.expanduser()
summary = json.loads((run / 'summary.json').read_text())
requests, incoming, goals = {}, {}, {}
last_goal = None
gaps = []
for line in (run / 'steps.jsonl').open():
    row = json.loads(line)
    event = row.get('event')
    if event == 'inference_requested':
        requests[row['origin']] = {
            **row,
            'preceding_goal_feedback': (last_goal or {}).get('feedback', {}).get('state'),
            'preceding_goal_time_s': (last_goal or {}).get('time_s'),
        }
    elif event == 'inference' and row.get('incoming_actions'):
        incoming[row['origin']] = row
    elif event == 'goal_transmitted':
        goals[row['action_index']] = row
        if last_goal is not None:
            gaps.append(row['time_s'] - last_goal['time_s'])
        last_goal = row
joint_names = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']
boundaries = []
for origin, chunk in sorted(incoming.items()):
    previous = goals.get(origin - 1)
    if previous is None:
        continue
    first = chunk['incoming_actions'][0]
    request = requests.get(origin, {})
    boundaries.append(dict(
        origin=origin,
        previous_goal_time_s=previous['time_s'],
        request_time_s=request.get('time_s'),
        remaining_at_request=request.get('remaining_actions'),
        input_state=request.get('observation_state'),
        preceding_request_feedback=request.get('preceding_goal_feedback'),
        preceding_request_feedback_time_s=request.get('preceding_goal_time_s'),
        previous_model=previous['model'], first_model=first,
        delta={name: first[i] - previous['model'][i] for i, name in enumerate(joint_names)},
        first_action_transmitted=origin in goals,
        inference_s=chunk['seconds'],
    ))
report = dict(
    run=str(run), status=summary.get('status'), reason=summary.get('reason'),
    steps=summary.get('steps'), elapsed_s=summary.get('elapsed_s'),
    physical_motor_writes=summary.get('physical_motor_writes'),
    passed=summary.get('passed'), functional_passed=summary.get('functional_passed'),
    timing_passed=summary.get('timing_passed'), raw_timing=summary.get('raw_timing'),
    chunk_wait_s=summary.get('chunk_wait_s'), camera=summary.get('camera'),
    boundary_count=len(boundaries),
    max_abs_boundary_delta={name: max((abs(b['delta'][name]) for b in boundaries), default=None)
                            for name in joint_names},
    goal_gap_s=dict(min=min(gaps, default=None), max=max(gaps, default=None)),
    boundaries=boundaries,
)
target = run / 'boundary_review.json'
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'boundaries'}, ensure_ascii=False))
