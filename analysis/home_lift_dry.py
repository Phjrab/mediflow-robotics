"""Real model/cameras, simulated arm with recorded home bias; serial is denied."""
import argparse
import json
from pathlib import Path
import sys

from alignment_metrics import deny_serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=180)
    parser.add_argument('--speed', type=float, default=.33)
    parser.add_argument('--bias-ticks', type=int, default=15)
    parser.add_argument('--loaded-lift', action='store_true',
                        help='Simulate the 11/18 measured home plateaus with a late-response scenario.')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.addaudithook(deny_serial)
    import startup_simulation as simulation
    import entry
    from home_lift_fixture import LoadedLiftArm
    from start_position_rules import home_targets

    original_arm = simulation.SimulatedArm
    original_start = simulation.start_policy_session
    original_monitor = simulation.Monitor

    def isolated_monitor(*values, **options):
        return original_monitor(*values, directory=args.out/'monitor', **options)

    class RecordedBiasArm(original_arm):
        def __init__(self, *values, **options):
            super().__init__(*values, **options)
            # Last failed home round, not a claim about current physical state.
            self.present = {**home_targets(), 'shoulder_lift': 856, 'elbow_flex': 3085}
            self.target = dict(self.present)

        def feedback(self):
            commanded = dict(self.target)
            if self.packet_count:
                self.target['shoulder_lift'] += args.bias_ticks
            try:
                sample = super().feedback()
            finally:
                self.target = commanded
            sample['active_targets'] = commanded
            return sample

    with (args.out / 'dry_trace.jsonl').open('x', encoding='utf-8') as trace:
        fixture = ('20261118_161810 plateaus with simulated movement past the cutoff'
                   if args.loaded_lift else '20261116_200615 final home result; simulated response thereafter')
        trace.write(json.dumps(dict(event='dry_trace_header', physical_motor_writes=0,
            serial_access='DENIED', simulated_lift_bias_ticks=None if args.loaded_lift else args.bias_ticks,
            fixture=fixture))+'\n')

        def observe(io, policy, home, log, *values, **options):
            def traced(row):
                log(row)
                trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            result = original_start(io, policy, home, traced, *values, **options)
            (args.out / 'policy_result.json').write_text(json.dumps(result,
                ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            return result

        simulation.SimulatedArm = LoadedLiftArm if args.loaded_lift else RecordedBiasArm
        simulation.start_policy_session = observe
        simulation.Monitor = isolated_monitor
        sys.argv = ['entry.py', '--dry-run', '--duration', str(args.duration), '--speed', str(args.speed)]
        try:
            return entry.main()
        finally:
            simulation.SimulatedArm = original_arm
            simulation.start_policy_session = original_start
            simulation.Monitor = original_monitor


if __name__ == '__main__':
    raise SystemExit(main())
