import json
from pathlib import Path

run = Path.home() / 'so101_vla/runs/DRY_RAW_1795002200901250804'
result = json.loads((run / 'summary.json').read_text())
last_goals = []
minimum_gap = None
last_time = None
request = inference = None
for line in (run / 'steps.jsonl').open():
    row = json.loads(line)
    if row['event'] == 'goal_transmitted':
        if last_time is not None:
            gap = row['time_s'] - last_time
            minimum_gap = gap if minimum_gap is None else min(minimum_gap, gap)
        last_time = row['time_s']
        last_goals.append({k: row.get(k) for k in ('action_index', 'time_s', 'model', 'wire_raw', 'observation_age_s')})
        last_goals = last_goals[-2:]
    elif row['event'] == 'inference_requested' and row['origin'] == 4900:
        request = row
    elif row['event'] == 'inference' and row['origin'] == 4900:
        inference = {k: row.get(k) for k in ('origin', 'seconds', 'time_s', 'delivery_observation_age_s', 'skipped_actions', 'overlap')}
        inference['first_action'] = row['incoming_actions'][0]
recording = result.get('grasp_recording') or {}
output = dict(
    status=result['status'], reason=result['reason'], steps=result['steps'],
    minimum_goal_gap_s=minimum_gap, last_goals=last_goals,
    next_chunk_request=request, next_chunk=inference,
    recorder={k: recording.get(k) for k in ('event_count', 'errors', 'writer_alive', 'worker', 'frames_saved', 'frames_dropped')},
    camera=result['camera'], physical_motor_writes=result['physical_motor_writes'],
    interpretation='The next chunk first action exceeded the unchanged 1.5x training maximum; no physical motor was opened. Current physical camera frames and simulated state are not a real closed-loop rollout.')
(run / 'review.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(output, ensure_ascii=False))
