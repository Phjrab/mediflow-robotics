"""Real SmolVLA + recorded camera frames + biased virtual servo; no hardware."""
import argparse
import json
from pathlib import Path
import sys
from alignment_metrics import deny_serial


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    sys.addaudithook(deny_serial)
    import numpy as np
    from PIL import Image
    import startup_simulation as simulation
    from trial_runtime import static_context
    from stream_live import make_policy
    from start_position_rules import home_targets
    original_arm=simulation.SimulatedArm
    original_start=simulation.start_policy_session

    class BiasedArm(original_arm):
        def __init__(self,*values,**options):
            super().__init__(*values,**options)
            self.present={**home_targets(),'shoulder_lift':856,'elbow_flex':3085}
            self.target=dict(self.present)

        def feedback(self):
            command=dict(self.target)
            if self.packet_count:self.target['shoulder_lift']+=15
            try:sample=super().feedback()
            finally:self.target=command
            sample['active_targets']=command
            return sample

    context=static_context()
    preview=Path(__file__).resolve().parents[1]/'analysis/vla_v3_blue_eval_preview'
    frames={name:{'pixels':np.array(Image.open(preview/f'training_{name}.png').convert('RGB'))}
            for name in simulation.CAMERAS}
    policy=make_policy(context)
    with (args.out/'trace.jsonl').open('x',encoding='utf-8') as trace:
        def observed(io,policy,home,log,*values,**options):
            def traced(row):
                log(row);trace.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            return original_start(io,policy,home,traced,*values,**options)
        simulation.SimulatedArm=BiasedArm
        simulation.start_policy_session=observed
        try:result=simulation.run_case('camera_dry',180,.33,context['home']['state'],frames,policy)
        finally:
            policy.close();simulation.SimulatedArm=original_arm
            simulation.start_policy_session=original_start
    result.update(input_source=str(preview),time_basis='virtual clock',serial_access='DENIED',
                  actual_model=True,actual_cameras=False,simulated_bias_ticks=15)
    (args.out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
