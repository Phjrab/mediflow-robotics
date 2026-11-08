"""저장된 프레임과 state만 정책에 전달한다. 로봇·카메라·serial 접근 없음."""
import json
from pathlib import Path
import sys
import time


def main():
    root=Path.home()/"v6_process_1793887709179"
    sys.path[:0]=[str(root/"operator_candidate"),str(root)]
    from first_trial_dependencies import prepare
    prepare()
    from process_policy import deny_hardware
    sys.addaudithook(deny_hardware)
    from trial_runtime import static_context,PolicyProvider
    from first_trial_core import JOINTS,CAMERAS,Limits,target_gate,Hold
    from runtime_cpu import configure_main
    from PIL import Image
    import numpy as np
    source=Path.home()/"first_vla_from_hold_1794125680799432750"
    out=Path(__file__).resolve().parent/"offline_result";out.mkdir(exist_ok=False)
    previous=json.loads((source/"RESULT.json").read_text(encoding="utf-8"))
    state=previous["inspection"]["snapshot"]["state"]
    bounds=previous["inspection"]["joint_bounds"]
    frames={name:dict(pixels=np.asarray(Image.open(source/(name+"_start.png")).convert("RGB")).copy()) for name in CAMERAS}
    configure_main(out/"CPU_RUNTIME.json")
    context=static_context();provider=None;runs=[]
    try:
        provider=PolicyProvider(context,previous["task"])
        for seed in range(3):
            provider.worker.call(provider.policy.reset,label="offline_reset")
            provider.worker.call(lambda:provider.policy.seed(seed),label="offline_seed")
            start=time.perf_counter();actions=provider.predict(frames,state);seconds=time.perf_counter()-start
            violations=[]
            for step,action in enumerate(actions):
                for i,value in enumerate(action):
                    lo,hi=bounds[i]
                    if not lo<=value<=hi:violations.append(dict(step=step,joint=JOINTS[i],value=value,bounds=[lo,hi],excess=max(lo-value,value-hi)))
            try:
                target_gate(actions[0],state,state,bounds,Limits(),1/30)
                first_gate="PASS"
            except Hold as error:first_gate=str(error)
            runs.append(dict(seed=seed,seconds=seconds,raw_actions=actions,first_action=actions[0],first_gate=first_gate,violations=violations))
    finally:
        if provider is not None:provider.close()
    result=dict(scope="SAVED_IMAGES_OFFLINE_RECALCULATION",original_actions_missing=True,
                exact_original_reproduction=False,source_state=state,source_bounds=bounds,task=previous["task"],runs=runs,
                hardware_access=False,motor_goal_packets=0,torque_register_writes=0,
                policy_process_alive=provider.policy.transport.process.is_alive() if provider is not None else None)
    with (out/"OFFLINE_REVIEW.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps(dict(status="OFFLINE_REVIEW_COMPLETE",hardware_access=False,policy_process_alive=result["policy_process_alive"],runs=[dict(seed=r["seed"],seconds=round(r["seconds"],4),first_action=r["first_action"],first_gate=r["first_gate"],violations=len(r["violations"]),first_violations=r["violations"][:3]) for r in runs]),ensure_ascii=False),flush=True)


if __name__=="__main__":main()
