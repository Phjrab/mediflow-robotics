"""Replay the saved live position samples into the new gate and a fake policy loop."""
import argparse
import json
from pathlib import Path

from config_runtime import load
from start_position_rules import HomeConvergence
from startup_simulation import SimulatedArm
from stream_live import start_policy_session


def startup_samples(trace, joints):
    group = {}
    with trace.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['event'] == 'startup_home_attempt':
                break
            if row['event'] == 'wire_tx' and row.get('instruction') == 130 and row.get('address') == 56:
                group = {}
            if (row['event'] != 'wire_rx' or row.get('operation') != 'read:56:15'
                    or row.get('communication') != 0 or not row.get('checksum_valid')):
                continue
            mid = row['expected_id']
            raw = bytes.fromhex(row['raw_hex'])
            if len(raw) != 21 or raw[:5] != bytes((255, 255, mid, 17, 0)) or sum(raw[2:])%256 != 255:
                raise ValueError('잘못된 위치 표본')
            group[joints[mid-1]] = int.from_bytes(raw[5:7], 'little')
            if len(group) == len(joints):
                yield row['monotonic_s'], group
                group = {}


def evaluate(run):
    config = load()
    window = HomeConvergence(c=config)
    samples = list(startup_samples(run/'steps.jsonl', config['joint_names']))
    ready = next(((raw,result) for stamp,raw in samples
                  if (result := window.observe(raw, stamp))['ready']), None)
    if ready is None:
        raise RuntimeError(f'{run.name}: 기록된 시작 자세 0.5초 조건 미충족')
    raw, observed = ready
    events = []

    class ReplayArm(SimulatedArm):
        def feedback(self):
            sample = super().feedback()
            if not getattr(self, 'motion_ready', False):
                sample.update(present_raw=dict(raw), state=self.normalize(raw))
            return sample

        def send(self, goal):
            if not getattr(self, 'motion_ready', False):
                raise AssertionError('기록 자세는 홈 이동 없이 통과해야 합니다')
            super().send(goal)

    io = ReplayArm(config['home']['state'], {}, events.append, 'camera_dry')
    io.present = dict(raw)
    io.target = dict(raw)
    policy = lambda *args: ([io.home[:]]*50, .02)
    result = start_policy_session(io, policy, io.home, events.append, .6, .33)
    return dict(run=run.name, observed_ticks=raw, recorded_stable_s=observed['stable_seconds'],
                status=result['status'], simulated_policy_steps=result['steps'],
                home_goal_packets=sum(r['event']=='startup_home_attempt' for r in events),
                physical_motor_writes=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    results = [evaluate(path) for path in args.runs]
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False))
    raise SystemExit(0 if all(r['status']=='RUN_COMPLETE' and r['simulated_policy_steps'] for r in results) else 1)
