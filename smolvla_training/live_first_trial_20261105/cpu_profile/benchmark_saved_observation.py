"""저장 관측만 사용하는 정책/후처리 비교. 로봇·카메라를 여는 기능 없음."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

from latency_dependencies import prepare
from fast_postprocess import bulk_actions,infer_chunk_fast,validate_post_config

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def statistics_row(values):
    values=sorted(values)
    return dict(mean=statistics.fmean(values),median=statistics.median(values),
                p95=values[max(0,math.ceil(len(values)*.95)-1)],max=max(values),count=len(values))

def deny_robot_device_open(event,args):
    if event!="open" or not args or not isinstance(args[0],(str,bytes,os.PathLike)):return
    path=os.fsdecode(args[0])
    if path.startswith(("/dev/tty","/dev/serial/","/dev/video","/dev/v4l/")):
        raise RuntimeError("오프라인 추론 프로세스의 로봇/카메라 장치 열기 거부")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--repeats",type=int,default=5)
    args=parser.parse_args()
    if not 3<=args.repeats<=10:raise ValueError("반복은3~10")
    out=args.out.expanduser().resolve()
    if out.exists() or out.parent!=Path(__file__).resolve().parent:raise ValueError("새 출력 폴더 필요")
    out.mkdir()
    sys.addaudithook(deny_robot_device_open)
    base=prepare()
    import policy_api as api
    import observation_contract as observation
    from checkpoint_amp import infer_with_saved_amp,saved_amp
    from persistent_policy_worker import PersistentPolicyWorker
    sample=base/"dry_output"
    checkpoint=Path.home()/"smolvla_model/pretrained_model"
    calibration=Path.home()/".cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json"
    api.verify_units(Path.home()/"units_so101.json",checkpoint,calibration)
    metadata=api.inspect_checkpoint(checkpoint)
    if metadata["checkpoint_kind"]!="SO101_FINETUNED_CANDIDATE":raise ValueError("학습 모델 미확인")
    config=json.loads((checkpoint/"config.json").read_text())
    if config.get("chunk_size")!=50 or config.get("n_action_steps")!=50:raise ValueError("50/50 chunk 계약 불일치")
    validate_post_config(json.loads((checkpoint/"policy_postprocessor.json").read_text()))
    contract=observation.load_contract(base/"resolved_camera_config.json")
    source_files=[sample/"state_start.json"]+[sample/(name+"_start.png") for name in api.CAMERAS]
    before={p.name:digest(p) for p in source_files}
    captured=json.loads(source_files[0].read_text())
    state=api.vector(captured["state"])
    os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
    import numpy as np
    from PIL import Image
    import torch
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    if not torch.cuda.is_available():raise RuntimeError("CUDA 없음")
    frames={}
    for name in api.CAMERAS:
        with Image.open(sample/(name+"_start.png")) as im:
            if im.mode!="RGB" or im.size!=(640,480):raise ValueError("저장 RGB 이미지 형식 불일치")
            frames[name]=np.asarray(im).copy()
    task="Put medicine bottle A into the blue basket"
    t=time.perf_counter();packed=observation.pack_observation(frames,state,task,torch,contract);pack_seconds=time.perf_counter()-t
    t=time.perf_counter();policy=SmolVLAPolicy.from_pretrained(str(checkpoint),local_files_only=True).to("cuda").eval()
    pre,post=make_pre_post_processors(policy.config,str(checkpoint),preprocessor_overrides={"device_processor":{"device":"cuda"}})
    load_seconds=time.perf_counter()-t
    if getattr(policy.config,"use_amp",None) is not saved_amp(config):raise ValueError("저장 AMP 불일치")
    worker=PersistentPolicyWorker();result={};timings=[];last={};profiles=[]
    try:
        def seeded():
            policy.reset();torch.manual_seed(101);torch.cuda.manual_seed_all(101)
        def check_parity():
            seeded()
            def predict():
                with torch.inference_mode():return policy.predict_action_chunk(pre(dict(packed)))
            normalized=infer_with_saved_amp(predict,torch,config)
            with torch.inference_mode(),torch.autocast(device_type="cuda",enabled=saved_amp(config)):
                legacy=torch.stack([post(normalized[:,step,:]).detach().float().cpu() for step in range(50)],dim=1)
                bulk=post(normalized).detach().float().cpu()
            if tuple(legacy.shape)!=(1,50,6) or tuple(bulk.shape)!=(1,50,6):raise ValueError("수치 대조 shape 오류")
            if not bool(torch.isfinite(legacy).all()) or not bool(torch.isfinite(bulk).all()):raise ValueError("수치 대조 NaN/Inf")
            delta=(legacy-bulk).abs()
            return dict(max_abs=float(delta.max()),joint_max_abs=delta.amax(dim=(0,1)).tolist(),
                        allclose=bool(torch.allclose(legacy,bulk,atol=1e-6,rtol=1e-6)))
        parity=worker.call(check_parity,label="actual_checkpoint_postprocessor_parity")
        if not parity["allclose"]:raise ValueError("저장 후처리 일괄 처리 결과 불일치")
        print(json.dumps({"postprocessor_parity":parity}),flush=True)
        def predict(mode,profile=False):
            seeded();torch.cuda.synchronize();start=time.perf_counter()
            fn=api.infer_chunk if mode=="legacy" else infer_chunk_fast
            actions=infer_with_saved_amp(lambda:fn(policy,pre,post,packed,torch),torch,config)
            torch.cuda.synchronize();elapsed=time.perf_counter()-start
            return actions,elapsed
        for mode in ("legacy","bulk"):
            for _ in range(2):worker.call(lambda mode=mode:predict(mode),label="warmup_"+mode)
        torch.cuda.reset_peak_memory_stats()
        for i in range(args.repeats):
            order=("legacy","bulk") if i%2==0 else ("bulk","legacy")
            for mode in order:
                actions,seconds=worker.call(lambda mode=mode:predict(mode),label="measured_"+mode)
                last[mode]=actions;timings.append(dict(mode=mode,repetition=i,seconds=seconds))
        def phase_profile(mode):
            seeded();torch.cuda.synchronize()
            with torch.inference_mode(),torch.autocast(device_type="cuda",enabled=saved_amp(config)):
                t=time.perf_counter();batch=pre(dict(packed));torch.cuda.synchronize();pre_s=time.perf_counter()-t
                t=time.perf_counter();normalized=policy.predict_action_chunk(batch);torch.cuda.synchronize();model_s=time.perf_counter()-t
                t=time.perf_counter()
                if mode=="bulk":actions=bulk_actions(post,normalized)
                else:
                    actions=[]
                    for i in range(50):actions.extend(post(normalized[:,i,:]).detach().float().cpu().tolist())
                torch.cuda.synchronize();post_s=time.perf_counter()-t
            return dict(mode=mode,preprocess_seconds=pre_s,model_seconds=model_s,postprocess_seconds=post_s,
                        profiling_synchronizations_added=True)
        profiles=[worker.call(lambda mode=mode:phase_profile(mode),label="profile_"+mode) for mode in ("legacy","bulk")]
        stats={mode:statistics_row([r["seconds"] for r in timings if r["mode"]==mode]) for mode in ("legacy","bulk")}
        actions=last["bulk"]
        if len(actions)!=50 or any(len(a)!=6 or any(not math.isfinite(x) for x in a) for a in actions):raise ValueError("최종 action50x6 오류")
        issues=[]
        if any(not 0<=a[5]<=100 for a in actions):issues.append("GRIPPER_OUT_OF_0_100")
        if any(abs(actions[0][i]-state[i])>10 for i in range(5)):issues.append("FIRST_BODY_DELTA_OVER_10_DEG")
        result=dict(status="OFFLINE_COMPUTE_PASS_WITH_WARNINGS" if issues else "OFFLINE_COMPUTE_PASS",
                    sample_source="PREVIOUS_READ_ONLY_CAPTURE_NOT_CURRENT_ROBOT",source_hashes=before,
                    source_unchanged=before=={p.name:digest(p) for p in source_files},parity=parity,
                    latency=stats,stage_profiles=profiles,capture_to_tensor_seconds=pack_seconds,model_load_seconds=load_seconds,
                    peak_gpu_bytes=torch.cuda.max_memory_allocated(),saved_use_amp=config["use_amp"],autocast_dtype=str(torch.get_autocast_dtype("cuda")),
                    model_weights_converted=False,chunk_shape=[50,6],first_action=actions[0],captured_state=state,
                    first_delta=[actions[0][i]-state[i] for i in range(6)],issues=issues,seed=101,
                    live_budget_seconds=50/30,bulk_all_within_live_budget=all(r["seconds"]<=50/30 for r in timings if r["mode"]=="bulk"),
                    robot_camera_serial_opened=False,register_writes=0,live_authorized=False,
                    limitation="분리 오프라인 계산 결과. 실시간 감시 포함 live 지연/안전/성공률 근거 아님")
        with (out/"latency.csv").open("x",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=["mode","repetition","seconds"]);w.writeheader();w.writerows(timings)
        with (out/"action_chunk.csv").open("x",newline="",encoding="utf-8") as f:
            w=csv.writer(f);w.writerow(["step",*api.JOINTS]);w.writerows([[i,*row] for i,row in enumerate(actions)])
    except Exception as error:
        result=dict(status="OFFLINE_FAILED",kind=type(error).__name__,reason=str(error).replace(str(Path.home()),"~"),register_writes=0,live_authorized=False)
    finally:
        result["policy_worker_closed"]=worker.close()
        with (out/"OFFLINE_RESULT.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result["status"].startswith("OFFLINE_COMPUTE_PASS") else 2

if __name__=="__main__":raise SystemExit(main())
