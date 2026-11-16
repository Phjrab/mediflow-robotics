"""Run the existing camera/model dry entry with an observational JSONL trace."""
import argparse
import json
from pathlib import Path
import sys
from alignment_metrics import deny_serial


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--duration',type=float,default=180);parser.add_argument('--speed',type=float,default=.33)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    sys.addaudithook(deny_serial)
    import startup_simulation
    import entry
    original=startup_simulation.start_policy_session
    with (args.out/'dry_trace.jsonl').open('x',encoding='utf-8') as target:
        target.write(json.dumps({'event':'dry_trace_header','motor_bus':'simulated','physical_motor_writes':0})+'\n')
        def observe(*values,**options):
            io,policy,home,log,*rest=values
            def traced(row):
                log(row)
                target.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            return original(io,policy,home,traced,*rest,**options)
        startup_simulation.start_policy_session=observe
        sys.argv=['entry.py','--dry-run','--duration',str(args.duration),'--speed',str(args.speed)]
        try:return entry.main()
        finally:startup_simulation.start_policy_session=original


if __name__=='__main__':raise SystemExit(main())
