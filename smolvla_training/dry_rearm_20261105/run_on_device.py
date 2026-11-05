"""Jetson 1회 점검→읽기 전용 확인서→v6 dry-run. live 실행 기능 없음."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parent

def clean(s):
    return s.replace(str(Path.home()),"~")

def main():
    os.chdir(ROOT)
    if (ROOT/"RUN_STEPS.json").exists() or (ROOT/"current_state.json").exists():
        raise RuntimeError("이미 실행한 폴더: 새 진단 없이 확인서를 재사용하지 않음")
    import device_preflight as devices
    import dry_session as ds
    rows=[];start=time.time()
    result={"status":"BLOCKED","live":"NOT_RUN","register_writes":"UNVERIFIED"}
    try:
        t=time.time()
        resolved=subprocess.run([sys.executable,"-B","resolve_rgb_camera.py"],capture_output=True,text=True,timeout=15)
        with (ROOT/"resolve_camera.log").open("x",encoding="utf-8") as f:f.write(clean(resolved.stdout+resolved.stderr))
        rows.append({"step":"resolve_rgb","exit_code":resolved.returncode,"seconds":time.time()-t})
        if resolved.returncode!=0:raise RuntimeError("동일 카메라 RGB 경로 검증 실패; resolve_camera.log 참조")
        before=devices.require()
        (ROOT/"device_before.json").write_text(json.dumps(before,indent=2))
        steps=[("probe",[sys.executable,"-B","probe_current_state.py","--out",str(ROOT/"current_state.json")],30),
               ("receipt",[sys.executable,"-B","dry_session.py","--probe",str(ROOT/"current_state.json"),"--out",str(ROOT/"dry_session.json")],30),
               ("v6_dry",[sys.executable,"-B","so101_first_action_test_safe_v6_dry.py","--dry-run","--dry-session",str(ROOT/"dry_session.json"),
                           "--robot-id","so-101","--unit-evidence",str(Path.home()/"units_so101.json"),
                           "--home-state",str(Path.home()/"home_state_so101.json"),"--checkpoint",str(Path.home()/"smolvla_model/pretrained_model"),
                           "--task","Put medicine bottle A into the blue basket","--max-seconds","5","--out",str(ROOT/"dry_output")],180)]
        for name,command,timeout in steps:
            t=time.time()
            try:
                r=subprocess.run(command,capture_output=True,text=True,timeout=timeout,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1"))
                log=clean(r.stdout+r.stderr);code=r.returncode
            except subprocess.TimeoutExpired as error:
                log="외부 시간 상한: 이 실행은 성공으로 처리하지 않음";code=124
            with (ROOT/(name+".log")).open("x",encoding="utf-8") as f:f.write(log)
            row={"step":name,"exit_code":code,"seconds":time.time()-t};rows.append(row)
            print(json.dumps(row),flush=True)
            if code!=0:
                result.update(status="HOLD",failed_step=name)
                break
        else:result["status"]="READ_ONLY_DRY_FINISHED"
        out=ROOT/"dry_output/DRY_RESULT.json"
        if out.is_file():
            dry=json.loads(out.read_text())
            result.update(dry_result=dry,register_writes=dry["register_writes"])
    except Exception as error:
        result.update(error={"kind":type(error).__name__,"reason":clean(str(error))})
    finally:
        try:after=devices.inspect()
        except Exception as error:after={"all_safe":False,"error":type(error).__name__}
        result["device_after"]=after
        result["stop_inventory_after"]=ds.stop_inventory()
        result["stop_archive_preserved"]=result["stop_inventory_after"]=={ds.STOP_NAME:ds.STOP_HASH}
        result.update(steps=rows,total_seconds=time.time()-start)
        if not after["all_safe"] or not result["stop_archive_preserved"]:result["status"]="HOLD_CLEANUP_OR_RECORDS"
        with (ROOT/"RUN_STEPS.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        with zipfile.ZipFile(ROOT/"run_evidence.zip","x",compression=zipfile.ZIP_DEFLATED) as z:
            for pattern in ("*.json","*.log","dry_output/*.json","dry_output/*.csv","dry_output/*_start.png"):
                for p in ROOT.glob(pattern):z.write(p,p.relative_to(ROOT))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result["status"]=="READ_ONLY_DRY_FINISHED" else 2

if __name__=="__main__":raise SystemExit(main())
