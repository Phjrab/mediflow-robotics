"""Offline evidence audit only: reads saved JSON/JSONL, never opens hardware.

Run with a completed dry-run directory. JSON is printed to stdout; an optional
--out writes a new audit artifact, never changes the run's summary or trace.
Functional completion, timing quality, and trace-integrity verdicts are separate.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def command_limits(config):
    limits = {joint: tuple(values) for joint, values in config['limits'].items()}
    low, high = config['calibration_ranges']['gripper']
    cap = math.floor(low + config['gripper']['max_open_points'] * (high-low) / 100)
    limits['gripper'] = limits['gripper'][0], min(limits['gripper'][1], cap)
    return limits


def encode_dry(values, config):
    """Independent reconstruction of SimulatedArm.plan's round + raw clipping."""
    result = {}
    limits = command_limits(config)
    for index, (joint, value) in enumerate(zip(JOINTS, values)):
        low, high = config['calibration_ranges'][joint]
        raw = round(value * 4095 / 360 + (low+high)/2) if index < 5 else round(low + value*(high-low)/100)
        result[joint] = min(limits[joint][1], max(limits[joint][0], raw))
    return result


def audit_rows(rows, summary, config, reference):
    errors = {}
    examples = {}
    def fail(kind, detail):
        errors[kind] = errors.get(kind, 0) + 1
        if len(examples.setdefault(kind, [])) < 5:
            examples[kind].append(detail)

    chunks, requests, goals, boundaries, safety = {}, [], [], [], []
    gaps, previous_wire, previous_wire_at, last_goal = [], None, None, None
    raw_limits = command_limits(config)
    n_steps = summary.get('n_action_steps', 50)
    fps = reference['fps']
    threshold = reference['raw_guard_1_5']
    rejected = []
    for number, row in enumerate(rows, 1):
        event = row.get('event')
        if event == 'inference_requested':
            requests.append(row)
            if row.get('remaining_actions') != 0:
                fail('prefetch_before_chunk_finished', dict(line=number, remaining=row.get('remaining_actions')))
            expected_origin = 0 if last_goal is None else last_goal['action_index']+1
            if row.get('origin') != expected_origin:
                fail('request_origin', dict(line=number, origin=row.get('origin'), expected=expected_origin))
            if last_goal is not None and row['time_s'] < last_goal['time_s']:
                fail('observation_before_last_goal', dict(line=number, request=row['time_s'], goal=last_goal['time_s']))
            if row.get('scheduling') != 'fresh_observation_after_chunk_exhausted':
                fail('request_schedule_label', dict(line=number, scheduling=row.get('scheduling')))
        elif event == 'inference' and row.get('incoming_actions') is not None:
            origin = row['origin']
            if origin in chunks:
                fail('duplicate_chunk_origin', dict(line=number, origin=origin))
            chunks[origin] = row
            if row.get('skipped_actions') != 0 or row.get('overlap') != 0:
                fail('action_drop_or_blend', dict(line=number, skipped=row.get('skipped_actions'), overlap=row.get('overlap')))
            if len(row['incoming_actions']) < n_steps:
                fail('short_chunk', dict(line=number, origin=origin))
        elif event in ('goal_transmitted', 'gripper_safety_goal'):
            wire, at = row['wire_raw'], row['time_s']
            if previous_wire_at is not None:
                gap = at-previous_wire_at
                gaps.append(dict(seconds=gap, from_event=previous_event, to_event=event, time_s=at))
                if gap < 1/fps-1e-6:
                    fail('wire_burst', dict(line=number, gap_s=gap, minimum_s=1/fps))
            if set(wire) != set(JOINTS) or any(type(v) is not int for v in wire.values()):
                fail('wire_format', dict(line=number, wire=wire))
            for joint in JOINTS:
                if not raw_limits[joint][0] <= wire[joint] <= raw_limits[joint][1]:
                    fail('wire_bounds', dict(line=number, joint=joint, target=wire[joint], bounds=raw_limits[joint]))
            if event == 'goal_transmitted':
                index = row['action_index']
                if index != len(goals):
                    fail('policy_index_sequence', dict(line=number, index=index, expected=len(goals)))
                origin = index-index % n_steps
                chunk = chunks.get(origin)
                original = None if chunk is None else chunk['incoming_actions'][index-origin]
                if original is None or list(original) != row['model']:
                    fail('model_action_changed', dict(line=number, index=index, original=original, sent_model=row['model']))
                if index and index % n_steps == 0 and last_goal is not None:
                    delta = {j: row['model'][i]-last_goal['model'][i] for i, j in enumerate(JOINTS)}
                    boundaries.append(dict(origin=index, time_s=at, delta=delta))
                if last_goal is not None:
                    for i, joint in enumerate(JOINTS):
                        delta = abs(row['model'][i]-last_goal['model'][i])
                        if delta > threshold[joint]+1e-8:
                            fail('transmitted_model_jump', dict(line=number, joint=joint, delta=delta, limit=threshold[joint]))
                protected = list(row['model'])
                offset = row.get('lateral_correction', {}).get('applied_deg', 0.)
                if abs(offset) > abs(config['lateral_correction']['offset_deg'])+1e-8:
                    fail('unapproved_pan_offset', dict(line=number, offset_deg=offset))
                if offset and row.get('lateral_correction', {}).get('reason') != 'grasp_window':
                    fail('pan_offset_outside_grasp', dict(line=number, offset_deg=offset))
                protected[0] += offset
                opening = row.get('gripper_opening', {})
                protected[-1] = opening.get('limited_points', protected[-1])
                if (not row.get('grip_relief') and opening.get('load_hold_points') is None
                        and protected[-1] != min(row['model'][-1], config['gripper']['max_open_points'])):
                    fail('unexplained_gripper_change', dict(line=number, model=row['model'][-1], protected=protected[-1]))
                expected_wire = encode_dry(protected, config)
                if wire != expected_wire:
                    fail('wire_not_quantized_protected_model', dict(line=number, actual=wire, expected=expected_wire))
                goals.append(row)
                last_goal = row
            else:
                safety.append(row)
                if previous_wire is None or any(wire[j] != previous_wire[j] for j in JOINTS[:-1]):
                    fail('safety_changed_other_axes', dict(line=number, previous=previous_wire, current=wire))
                if previous_wire is not None and wire['gripper'] == previous_wire['gripper']:
                    fail('duplicate_safety_goal', dict(line=number, wire=wire))
                if (row.get('policy_index_advanced') is not False or row.get('step') != len(goals)
                        or row.get('action_index') != len(goals)-1):
                    fail('safety_advanced_policy_index', dict(line=number, step=row.get('step'), index=row.get('action_index')))
                if row.get('reason') not in ('opening_load_freeze', 'closing_relief'):
                    fail('unrecognized_safety_goal', dict(line=number, reason=row.get('reason')))
            previous_wire, previous_wire_at, previous_event = wire, at, event
        elif event == 'raw_guard_rejected':
            rejected.append(row)

    for key, expected in (('steps', len(goals)), ('gripper_safety_goal_packets', len(safety)),
                          ('skipped_policy_actions', 0), ('physical_motor_writes', 0)):
        if summary.get(key) != expected:
            fail('summary_' + key, dict(recorded=summary.get(key), counted_or_required=expected))
    if summary.get('interpolation') is not False or summary.get('speed_scaling') is not False:
        fail('raw_execution_mode', dict(interpolation=summary.get('interpolation'), scaling=summary.get('speed_scaling')))
    protection = dict(stop_c=config['temperature']['stop_c'], window_s=config['temperature']['window_s'],
                      continuous_hot_s=config['temperature']['continuous_hot_s'],
                      gripper_cap=config['gripper']['max_open_points'], elbow_upper=raw_limits['elbow_flex'][1],
                      communication=config['communication'], jump_thresholds=threshold)
    required = dict(stop_c=55, window_s=2, continuous_hot_s=2, gripper_cap=48.5, elbow_upper=3084)
    for key, expected in required.items():
        if protection[key] != expected:
            fail('protection_contract', dict(key=key, value=protection[key], expected=expected))
    expected_comm = dict(max_retries=3, consecutive_failures=5, no_response_s=.2, hold_attempts=5,
                         bulk_address=56, bulk_width=15, read_order=[1,2,3,4,6,5])
    if protection['communication'] != expected_comm:
        fail('communication_contract', protection['communication'])
    elapsed = summary.get('elapsed_s', 0.)
    measured_progress = len(goals)/elapsed/fps if elapsed else None
    control = (summary.get('raw_timing') or {}).get('control_hz', {})
    p5 = control.get('p5')
    timing_pass = p5 is not None and p5 >= 25 and measured_progress is not None and measured_progress >= .8
    if summary.get('timing_passed') != timing_pass:
        fail('timing_verdict_mismatch', dict(reported=summary.get('timing_passed'), recomputed=timing_pass))
    duration = (summary.get('ready') or {}).get('duration_s')
    arm_error = summary.get('max_arm_error_ticks')
    functional_pass = (summary.get('status') == 'RUN_COMPLETE' and len(goals) > 0
                       and duration is not None and elapsed >= duration-.05
                       and not summary.get('camera_cleanup_errors')
                       and arm_error is not None and arm_error <= 3)
    if summary.get('functional_passed') != functional_pass:
        fail('functional_verdict_mismatch', dict(reported=summary.get('functional_passed'), recomputed=functional_pass))
    if summary.get('passed') != (functional_pass and timing_pass):
        fail('combined_verdict_mismatch', dict(reported=summary.get('passed'), recomputed=functional_pass and timing_pass))
    goals_by_index = {row['action_index']: row for row in goals}
    proposed_boundaries = []
    for origin, chunk in sorted(chunks.items()):
        preceding = goals_by_index.get(origin-1)
        if preceding is None:
            continue
        first = chunk['incoming_actions'][0]
        proposed_boundaries.append(dict(origin=origin, first_action_transmitted=origin in goals_by_index,
                                        delta={j:first[i]-preceding['model'][i] for i,j in enumerate(JOINTS)}))
    return dict(audit_passed=not errors, error_counts=errors, error_examples=examples,
                functional_passed=functional_pass, timing_passed=timing_pass,
                combined_passed=summary.get('passed'), status=summary.get('status'), reason=summary.get('reason'),
                steps=len(goals), elapsed_s=elapsed, physical_motor_writes=summary.get('physical_motor_writes'),
                inference_requests=len(requests), requests_remaining_all_zero=all(r.get('remaining_actions') == 0 for r in requests),
                boundary_count=len(proposed_boundaries), transmitted_boundary_count=len(boundaries),
                max_abs_boundary_delta={j:max((abs(b['delta'][j]) for b in proposed_boundaries),default=None) for j in JOINTS},
                max_abs_transmitted_boundary_delta={j:max((abs(b['delta'][j]) for b in boundaries),default=None) for j in JOINTS},
                boundaries=proposed_boundaries, rejected_boundaries=rejected,
                policy_actions_exact=not errors.get('model_action_changed'),
                wire_targets_match_reported_protections=not errors.get('wire_not_quantized_protected_model'),
                gripper_safety_goal_packets=len(safety),
                all_logged_goal_gap_s=dict(count=len(gaps), minimum=min((g['seconds'] for g in gaps),default=None),
                                          maximum=max((g['seconds'] for g in gaps),default=None)),
                overall_action_hz=len(goals)/elapsed if elapsed else None, action_progress_ratio=measured_progress,
                control_hz=control, active_chunk_transmission_hz=summary.get('active_chunk_transmission_hz'),
                inference_s=summary.get('inference_s'), chunk_wait_s=summary.get('chunk_wait_s'),
                protection_contract=protection,
                scope_notes=['Goal gaps include policy and gripper safety goals; startup and terminal HOLD are separate.',
                             'Model actions are compared exactly with logged inference chunks, before quantization/protection.',
                             'Wire encoding is independently reconstructed for SimulatedArm (round then clip), not physical SDK truncation.',
                             'History-dependent gripper timers are covered by unit tests; this audit checks their logged targets and scope.',
                             'Protection configuration is checked locally; matching deployment hashes must be verified separately.',
                             'Real camera images with simulated motor state do not prove real grasp success.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT/'live/config.yaml')
    parser.add_argument('--reference', type=Path, default=ROOT/'analysis/raw_action_reference.json')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    run = args.run.expanduser()
    summary = read_json(run/'summary.json')
    config, reference = read_json(args.config), read_json(args.reference)
    with (run/'steps.jsonl').open(encoding='utf-8-sig') as stream:
        report = audit_rows((json.loads(line) for line in stream if line.strip()), summary, config, reference)
    report.update(run=str(run), config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
                  reference_sha256=hashlib.sha256(args.reference.read_bytes()).hexdigest())
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    if args.out:
        with args.out.open('x', encoding='utf-8') as target:
            target.write(rendered)
    print(rendered, end='')
    return 0 if report['audit_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
