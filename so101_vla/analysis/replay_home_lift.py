"""Compare home controllers on a fitted static-offset servo, not physical replay."""
import argparse
import importlib.util
import json
from pathlib import Path

from test_home_trim import BiasedHomeArm
import live_start
from start_position_rules import position_check


def evaluate(controller, bias, **options):
    arm = BiasedHomeArm(bias, **options)
    reason = None
    try:
        controller.prepare(arm, arm.home, arm.record)
    except Exception as exc:
        reason = str(exc)
    position = position_check(arm.checked()['present_raw'])
    return dict(status='HOME_PASS' if reason is None else 'HOLD', reason=reason,
        simulated_bias_ticks=bias, physical_motor_writes=0,
        attempts=[dict(attempt=r['attempt'], command=r.get('command_ticks',r['target_ticks'])['shoulder_lift'])
                  for r in arm.events if r['event']=='startup_home_attempt'],
        trim=getattr(arm, 'home_command_trim', {}),
        final_lift=position['joints']['shoulder_lift'], events=arm.events)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('home_before_fix',args.before)
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    cases={}
    for bias in (14,15,-14,25):
        cases[str(bias)]=dict(before=evaluate(before,bias),after=evaluate(live_start,bias))
    cases['frozen']=dict(after=evaluate(live_start,14,frozen=True))
    cases['moving']=dict(after=evaluate(live_start,30,moving=True))
    result=dict(method='First-order simulated servo with fitted static equilibrium offset; not hardware proof',
                evidence='20261116_200615 home residuals 14/15/14 ticks', cases=cases)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for case,pair in cases.items():
        print(json.dumps(dict(case=case,results={k:dict(status=v['status'],attempts=v['attempts'],
            final_lift=v['final_lift']['present_ticks'],residual=v['final_lift']['residual_ticks'])
            for k,v in pair.items()}),ensure_ascii=False))


if __name__=='__main__':main()
