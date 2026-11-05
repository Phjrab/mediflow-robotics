"""실행 준비 코드의 정책 provider만 저장 입력으로 확인. 장치 접근 금지."""
import csv
import json
from pathlib import Path
import sys
from trial_runtime import dependencies
base=dependencies()
from process_policy import deny_hardware
sys.addaudithook(deny_hardware)

def main():
    from trial_runtime import static_context,PolicyProvider
    from runtime_cpu import configure_main
    import numpy as np
    from PIL import Image
    root=Path(__file__).parent
    configure_main(root/"PROVIDER_CPU.json")
    context=static_context();sample=base/"dry_output"
    state=json.loads((sample/"state_start.json").read_text())["state"]
    frames={}
    for name in ("end_effector","ceiling_vertical","ceiling_oblique"):
        with Image.open(sample/(name+"_start.png")) as image:frames[name]=dict(pixels=np.asarray(image).copy())
    reference=Path.home()/"first_trial_cpu_20261105_1793884175793/offline/action_chunk.csv"
    joints=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")
    expected=[[float(row[j]) for j in joints] for row in csv.DictReader(reference.open(encoding="utf-8",newline=""))]
    provider=PolicyProvider(context,"Put medicine bottle A into the blue basket")
    try:
        provider.worker.call(provider.policy.reset,label="parity_reset")
        provider.worker.call(lambda:provider.policy.seed(101),label="parity_seed")
        actions=provider.predict(frames,state)
        if len(actions)!=50 or len(expected)!=50:raise RuntimeError("행동50개 필요")
        diffs=[abs(a-b) for row,old in zip(actions,expected) for a,b in zip(row,old)]
        parity=all(abs(a-b)<=1e-6+1e-6*abs(b) for row,old in zip(actions,expected) for a,b in zip(row,old))
        if not parity:raise RuntimeError("연결된 provider 수치 불일치")
        result=dict(status="PROVIDER_OFFLINE_PASS",max_abs=max(diffs),chunk_shape=[50,6],register_writes=0,hardware_opened=False,live="NOT_RUN")
    finally:provider.close()
    result["child_alive"]=provider.policy.transport.process.is_alive()
    with (root/"PROVIDER_OFFLINE_RESULT.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
