"""고정3회 독립 v6 dry-run. 실패를 숨기지 않고 원본 결과를 보존한다."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
import argparse

ROOT=Path(__file__).resolve().parent
SOURCE=Path.home()/"v6_latency_20261105_1793880265851"
FILES=("latency_dependencies.py","fast_postprocess.py","build_fast_variant.py","test_latency.py",
       "benchmark_saved_observation.py","run_v6_fast_validation.py","batch_motor_read.py","check_batch_read.py","dependencies.json")

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--source",type=Path,default=SOURCE);args=parser.parse_args()
    source=args.source.expanduser().resolve()
    files=list(FILES)+(["cpu_runtime.py"] if (source/"cpu_runtime.py").is_file() else [])
    runs=ROOT/"runs";runs.mkdir(exist_ok=False)
    rows=[]
    for i in range(3):
        auth=subprocess.run(["sudo","-n","-v"],capture_output=True)
        if auth.returncode:rows.append(dict(index=i,status="AUTH_QUERY_BLOCKED"));break
        out=runs/("case_%02d"%i);out.mkdir()
        for name in files:shutil.copyfile(source/name,out/name)
        (out/"offline").mkdir();shutil.copyfile(source/"offline/OFFLINE_RESULT.json",out/"offline/OFFLINE_RESULT.json")
        start=time.time()
        try:
            p=subprocess.run([str(Path.home()/"venvs/smolvla-py310/bin/python"),"-B","run_v6_fast_validation.py"],
                             cwd=out,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=220)
            with (out/"execution.log").open("x",encoding="utf-8") as f:f.write((p.stdout+p.stderr).replace(str(Path.home()),"~"))
            result=json.loads((out/"FAST_VALIDATION.json").read_text())
            dry=result.get("dry_result",{})
            row=dict(index=i,status=result["status"],exit_code=p.returncode,seconds=time.time()-start,
                     error=dry.get("error"),register_writes=dry.get("register_writes"),
                     post_checks=result.get("device_after",{}).get("all_safe"),record_preserved=result.get("records_preserved"),
                     inference_seconds=(dry.get("gpu_worker_history") or [{}])[-1].get("seconds"))
            if row["status"]!="V6_FAST_DRY_COMPLETE":
                reason=(dry.get("error") or result.get("error") or {}).get("reason","")
                harmless_latency_hold=(row["register_writes"]==0 and row["post_checks"] is True and row["record_preserved"] is True
                  and any(w in reason for w in ("추론","시간 상한","감시 주기 지연")))
                if not harmless_latency_hold:
                    rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True);break
        except Exception as error:
            row=dict(index=i,status="BLOCKED",error=type(error).__name__,reason=str(error).replace(str(Path.home()),"~"))
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True);break
        rows.append(row)
        with (ROOT/"REPEAT_EVENTS.jsonl").open("a",encoding="utf-8") as f:f.write(json.dumps(row,ensure_ascii=False)+"\n")
        print(json.dumps(row,ensure_ascii=False),flush=True)
    summary=dict(planned=3,completed=len(rows),passed=sum(r["status"]=="V6_FAST_DRY_COMPLETE" for r in rows),cases=rows,
                 live="NOT_RUN",all_pass=len(rows)==3 and all(r["status"]=="V6_FAST_DRY_COMPLETE" for r in rows))
    with (ROOT/"REPEAT_SUMMARY.json").open("x",encoding="utf-8") as f:json.dump(summary,f,ensure_ascii=False,indent=2)
    with zipfile.ZipFile(ROOT/"repeat_evidence.zip","x",compression=zipfile.ZIP_DEFLATED) as z:
        for p in ROOT.rglob("*"):
            if p.is_file() and p.suffix in (".json",".csv",".log",".jsonl"):
                z.write(p,p.relative_to(ROOT))
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary["all_pass"] else 2

if __name__=="__main__":raise SystemExit(main())
