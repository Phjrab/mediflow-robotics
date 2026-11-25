"""Operator-attended one-episode trial. Enter saves; Ctrl+C discards the partial episode."""
import json
from pathlib import Path
import select
import signal
import sys
import time
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8022"


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(BASE+path,data=data,headers={"Content-Type":"application/json"}),timeout=12) as r:
        if r.headers.get("X-LeLab-Recording-Guard") != "1":
            raise RuntimeError("Guarded service is required")
        return json.load(r)


def main():
    if not sys.stdin.isatty():
        raise SystemExit("Run in an attended Jetson terminal")
    if request("/recording-status")["recording_active"]:
        raise SystemExit("A recording is already active")
    stop = False
    def interrupted(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT,interrupted)
    # Each test has a dedicated name on the existing, timestamped LeLab path.
    from record_guard import site_config
    site=site_config()
    name=site["trial_dataset"]
    body=dict(leader_config="so-101.json",follower_config="so-101.json",
              leader_port=site["ports"]["leader"],
              follower_port=site["ports"]["follower"],
              dataset_repo_id=name,single_task="Put medicine bottle B into the red basket",num_episodes=1,
              fps=30, push_to_hub=False, resume=False, episode_time_s=180,
              cameras={key:dict(type="opencv",camera_index=idx,width=640,height=480,fps=30)
                       for key,idx in (("end_effector",0),("ceiling_vertical",8),("ceiling_oblique",6))})
    if stop:
        return
    result=request("/guarded-start-recording",body)
    if not result.get("success"):
        raise SystemExit(str(result))
    expected=result["dataset_id"]
    print("Enter after placement to save, or Ctrl+C to stop. Waiting for torque readback.",flush=True)
    stop_sent=False
    save_sent=False
    while True:
        status=request("/recording-status")
        repo=status.get("dataset_repo_id","")
        if not repo.startswith(name+"_") or (expected and expected!=repo):
            raise RuntimeError("The current recording no longer matches this trial")
        expected=repo
        if not status["recording_active"]:
            break
        if stop and not stop_sent:
            request("/stop-recording",{})
            stop_sent=True
        elif not stop and not save_sent and status["current_phase"]=="recording" and select.select([sys.stdin],[],[],0)[0]:
            sys.stdin.readline()
            request("/recording-exit-early",{})
            save_sent=True
        time.sleep(.15)
    folder=Path.home()/".local/state/lelab-record-guard"
    for path in sorted(folder.glob("*.json"), key=lambda p:p.stat().st_mtime,reverse=True):
        report=json.loads(path.read_bytes())
        if report["dataset"]==expected:
            print(json.dumps(report,indent=2))
            if not report["all_off_verified"] or report["errors"] or status.get("current_phase")=="error":
                raise SystemExit("CHECK FAILED: "+str(status.get("error","see readback")))
            return
    raise SystemExit("No torque readback report available")


if __name__=="__main__":
    main()
