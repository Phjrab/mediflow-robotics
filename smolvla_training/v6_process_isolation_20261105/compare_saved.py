"""같은 저장 입력·seed·체크포인트로 프로세스 분리 전후 수치를 대조한다."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from latency_dependencies import prepare

def main():
    p=argparse.ArgumentParser();p.add_argument("--baseline",type=Path,required=True);p.add_argument("--out",type=Path,required=True);args=p.parse_args()
    out=args.out.expanduser();out.mkdir(exist_ok=False)
    base=prepare()
    from process_policy import ProcessPolicy,deny_hardware
    sys.addaudithook(deny_hardware)
    from runtime_cpu import configure_main
    configure_main(out/"CPU_RUNTIME.json")
    import policy_api as api,observation_contract as observation
    import numpy as np
    import torch
    from PIL import Image
    sample=base/"dry_output";baseline=args.baseline.expanduser()
    record=json.loads((baseline/"OFFLINE_RESULT.json").read_text())
    if record.get("status")!="OFFLINE_COMPUTE_PASS" or record.get("seed")!=101:raise RuntimeError("기존 수치 대조 근거 없음")
    source=[sample/"state_start.json"]+[sample/(name+"_start.png") for name in api.CAMERAS]
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source}
    if hashes!=record["source_hashes"]:raise RuntimeError("저장 관측이 기존 수치 대조와 다름")
    checkpoint=Path.home()/"smolvla_model/pretrained_model"
    calibration=Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    api.verify_units(Path.home()/"units_so101.json",checkpoint,calibration)
    contract=observation.load_contract(base/"resolved_camera_config.json")
    state=api.vector(json.loads(source[0].read_text())["state"])
    frames={}
    for name in api.CAMERAS:
        with Image.open(sample/(name+"_start.png")) as im:
            if im.mode!="RGB" or im.size!=(640,480):raise ValueError("저장 이미지 형식 불일치")
            frames[name]=np.asarray(im).copy()
    packed=observation.pack_observation(frames,state,"Put medicine bottle A into the blue basket",torch,contract)
    old=list(csv.DictReader((baseline/"action_chunk.csv").open(encoding="utf-8",newline="")))
    expected=[[float(row[j]) for j in api.JOINTS] for row in old]
    policy=None;result=dict(status="OFFLINE_FAILED",register_writes=0,live_authorized=False,robot_camera_serial_opened=False)
    try:
        policy=ProcessPolicy.from_pretrained(checkpoint,local_files_only=True)
        for _ in range(2):policy.predict_raw(packed)
        timings=[];parities=[]
        for i in range(3):
            policy.reset();policy.seed(101)
            start=time.perf_counter();actions=policy.predict_raw(packed);seconds=time.perf_counter()-start
            if len(actions)!=50 or any(len(row)!=6 or any(not math.isfinite(x) for x in row) for row in actions):raise ValueError("50x6 유한값 필요")
            diffs=[abs(x-y) for a,b in zip(actions,expected) for x,y in zip(a,b)]
            parity=all(abs(x-y)<=1e-6+1e-6*abs(y) for a,b in zip(actions,expected) for x,y in zip(a,b))
            parities.append(parity);timings.append(seconds)
            print(json.dumps(dict(repetition=i,seconds=seconds,parity=parity,max_abs=max(diffs))),flush=True)
        result.update(status="OFFLINE_COMPUTE_PASS" if all(parities) else "PARITY_FAILED",parity=dict(allclose=all(parities),max_abs=max(diffs)),
                      source_hashes=hashes,source_unchanged=hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source},
                      latency=dict(mean=statistics.fmean(timings),max=max(timings),seconds=timings),seed=101,chunk_shape=[50,6],source_reference="same_saved_observation_and_seed",
                      limitation="저장 입력 비교이며 실시간 감시 포함 통과나 약통 투입 성공률이 아님")
        with (out/"action_chunk.csv").open("x",encoding="utf-8",newline="") as f:
            writer=csv.writer(f);writer.writerow(["step",*api.JOINTS]);writer.writerows([[i,*row] for i,row in enumerate(actions)])
    except BaseException as e:result.update(kind=type(e).__name__,reason=str(e).replace(str(Path.home()),"~"))
    finally:
        if policy is not None:policy.close()
        result["child_alive"]=policy.transport.process.is_alive() if policy is not None else "NOT_STARTED"
        with (out/"OFFLINE_RESULT.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result["status"]=="OFFLINE_COMPUTE_PASS" else 2

if __name__=="__main__":raise SystemExit(main())
