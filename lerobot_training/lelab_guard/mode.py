"""Switch the 8022 service between the guarded recording overlay and the installed original."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

UNIT="lelab-jetson-py310.service"
ROOT=Path(__file__).resolve().parent
DROP=Path.home()/".config/systemd/user"/(UNIT+".d")/"61-mediflow-record-guard.conf"


def systemctl(*args):
    return subprocess.check_output(["systemctl","--user",*args],text=True).strip()


def get(route):
    with urlopen("http://127.0.0.1:8022/"+route,timeout=3) as r:
        return json.load(r), r.headers.get("X-LeLab-Recording-Guard")=="1"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["guarded","original"])
    enabled=parser.parse_args().mode=="guarded"
    state=systemctl("show",UNIT,"-p","ActiveState","--value")
    if state=="active":
        for route,field in [("recording-status","recording_active"),("teleoperation-status","teleoperation_active"),
                            ("inference-status","inference_active")]:
            status,_=get(route)
            if status[field]:
                raise SystemExit(f"Cannot switch during {route}")
    elif state not in ("inactive","failed"):
        raise SystemExit(f"Cannot switch unit in state {state}")
    if enabled:
        fragment=Path(systemctl("show",UNIT,"-p","FragmentPath","--value"))
        commands=[s for s in fragment.read_text().splitlines() if s.startswith("ExecStart=")]
        if len(commands)!=1 or commands[0].count("uvicorn lelab.server:app")!=1:
            raise SystemExit("Unexpected original service command")
        if any(not (c.isalnum() or c in "/_-.") for c in str(ROOT)):
            raise SystemExit("Unsupported install path")
        cmd=commands[0].replace("uvicorn lelab.server:app",f"uvicorn guarded_app:app --app-dir {ROOT}")
        DROP.parent.mkdir(parents=True,exist_ok=True)
        temporary=DROP.with_suffix(".tmp")
        temporary.write_text("[Service]\nExecStart=\n"+cmd+"\nTimeoutStopSec=120\n")
        os.replace(temporary,DROP)
    else:
        DROP.unlink(missing_ok=True)
    systemctl("daemon-reload")
    systemctl("restart",UNIT)
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        try:
            health,flag=get("health")
            if health.get("status")=="ok" and flag==enabled:
                print("recording_guard="+str(flag),flush=True)
                return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit("Service mode not verified; inspect the unit journal")


if __name__=="__main__":
    main()
