"""후처리 수치 검증 후 원래 v6 검사/감시를 유지한 새 dry-run만 실행."""
import json
import csv
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from latency_dependencies import prepare

ROOT=Path(__file__).resolve().parent

def run(name,command,timeout,rows):
    started=time.time()
    p=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout)
    with (ROOT/(name+".log")).open("x",encoding="utf-8") as f:f.write((p.stdout+p.stderr).replace(str(Path.home()),"~"))
    rows.append(dict(step=name,exit_code=p.returncode,seconds=time.time()-started))
    print(json.dumps(rows[-1]),flush=True)
    if p.returncode!=0:raise RuntimeError(name+" 실패: "+str(p.returncode))

def main():
    base=prepare();os.chdir(ROOT)
    from fast_postprocess import validate_post_config
    import device_preflight as devices
    import dry_session as ds
    if (ROOT/"FAST_VALIDATION.json").exists():raise RuntimeError("기존 실행 덮어쓰기 거부")
    offline=json.loads((ROOT/"offline/OFFLINE_RESULT.json").read_text())
    if offline.get("status")!="OFFLINE_COMPUTE_PASS" or offline.get("parity",{}).get("allclose") is not True or not offline.get("source_unchanged"):
        raise RuntimeError("오프라인 수치/입력 검증 실패: 실제 관측 재실행하지 않음")
    validate_post_config(json.loads((Path.home()/"smolvla_model/pretrained_model/policy_postprocessor.json").read_text()))
    rows=[];result={"status":"BLOCKED","live":"NOT_RUN"};stamp=str(time.time_ns())
    probe=base/("latency_probe_"+stamp+".json");receipt=base/("latency_receipt_"+stamp+".json")
    python=str(Path.home()/"venvs/smolvla-py310/bin/python")
    try:
        devices.require()
        run("build_fast",[python,"-B","build_fast_variant.py"],15,rows)
        run("current_probe",[python,"-B",str(base/"probe_current_state.py"),"--out",str(probe)],30,rows)
        run("batch_api_probe",[python,"-B","check_batch_read.py","--out",str(ROOT/"batch_api_probe.json")],30,rows)
        run("issue_session",[python,"-B",str(base/"dry_session.py"),"--probe",str(probe),"--out",str(receipt)],30,rows)
        run("v6_fast",[python,"-B","so101_v6_fast_dry.py","--dry-run","--dry-session",str(receipt),
                        "--robot-id","so-101","--unit-evidence",str(Path.home()/"units_so101.json"),
                        "--home-state",str(Path.home()/"home_state_so101.json"),"--checkpoint",str(Path.home()/"smolvla_model/pretrained_model"),
                        "--task","Put medicine bottle A into the blue basket","--max-seconds","5","--out",str(ROOT/"fast_dry_output")],180,rows)
        timing_rows=list(csv.DictReader((ROOT/"fast_dry_output/chunks.csv").open(encoding="utf-8",newline="")))
        if len(timing_rows)!=6 or any(float(row["inference_seconds"])>50/30 for row in timing_rows):
            raise RuntimeError("최종 상태 확인 포함 추론 시간/6관절 기록 조건 실패")
        result["status"]="V6_FAST_DRY_COMPLETE"
    except Exception as error:
        result.update(status="HOLD",error={"kind":type(error).__name__,"reason":str(error).replace(str(Path.home()),"~")})
    finally:
        p=ROOT/"fast_dry_output/DRY_RESULT.json"
        if p.is_file():result["dry_result"]=json.loads(p.read_text())
        if probe.is_file():result["probe"]=json.loads(probe.read_text())
        try:result["device_after"]=devices.inspect()
        except Exception as error:result["device_after"]={"all_safe":False,"error":type(error).__name__}
        result["stop_inventory"]=ds.stop_inventory();result["records_preserved"]=result["stop_inventory"]=={ds.STOP_NAME:ds.STOP_HASH}
        result["steps"]=rows
        if not result["device_after"]["all_safe"] or not result["records_preserved"]:result["status"]="HOLD_CLEANUP"
        with (ROOT/"FAST_VALIDATION.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        with zipfile.ZipFile(ROOT/"latency_evidence.zip","x",compression=zipfile.ZIP_DEFLATED) as z:
            for pattern in ("*.json","*.log","offline/*.json","offline/*.csv","fast_dry_output/*.json","fast_dry_output/*.csv","fast_dry_output/*.png"):
                for path in ROOT.glob(pattern):z.write(path,path.relative_to(ROOT))
    return 0 if result["status"]=="V6_FAST_DRY_COMPLETE" else 2

if __name__=="__main__":raise SystemExit(main())
