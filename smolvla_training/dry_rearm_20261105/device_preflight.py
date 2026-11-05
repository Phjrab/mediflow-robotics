"""장치를 열지 않는 서비스/점유 검사. Astra producer만 예외로 허용."""
import json
import os
from pathlib import Path
import subprocess
import observation_contract as observation
from camera_owner_contract import allowed_owners
from resolve_rgb_camera import identity,EXPECTED

def inspect():
    result={"devices":{}}
    for key,command in (("lelab",["systemctl","--user","is-active","lelab-jetson-py310.service"]),
                        ("astra",["systemctl","is-active","astra-v4l2-bridge.service"])):
        r=subprocess.run(command,capture_output=True,text=True,timeout=8)
        result[key]=r.stdout.strip()
    config=Path(__file__).resolve().parent/"resolved_camera_config.json"
    if not config.is_file():config=Path.home()/"nov_live_prep_20261104/vla_observation_config.json"
    contract=observation.load_contract(config)
    paths={"serial":"/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00",**contract["cameras"]}
    for role,device in paths.items():
        if not Path(device).exists():
            result["devices"][role]={"exists":False,"owners_ok":False};continue
        r=subprocess.run(["sudo","-n","fuser",device],capture_output=True,text=True,timeout=8,env=dict(os.environ,LC_ALL="C"))
        ok=allowed_owners(r.returncode,r.stdout,r.stderr,role=="ceiling_vertical",
                         lambda pid:(Path("/proc")/pid/"cgroup").read_text())
        if role=="ceiling_oblique":
            ok=ok and identity(device)=={k:v for k,v in EXPECTED.items() if k!='fourcc'}
        result["devices"][role]={"exists":True,"owners_ok":ok,"query_code":r.returncode}
    result["all_safe"]=result["lelab"]=="inactive" and result["astra"]=="active" and all(r["owners_ok"] for r in result["devices"].values())
    return result

def require():
    result=inspect()
    if not result["all_safe"]:raise RuntimeError("장치 사전검사 거부: "+json.dumps(result,ensure_ascii=False))
    return result
