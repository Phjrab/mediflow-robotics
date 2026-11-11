"""장치를 열지 않는 서비스/점유 검사. Astra producer만 예외로 허용."""
import json
import os
from pathlib import Path
import subprocess
import observation_contract as observation
from camera_owner_contract import allowed_owners
from resolve_rgb_camera import identity,EXPECTED

def inspect():
    from config_runtime import load
    c=load();result={'devices':{}}
    for key,cmd in [('lelab',['systemctl','--user','is-active','lelab-jetson-py310.service']),('astra',['systemctl','is-active','astra-v4l2-bridge.service'])]:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=8);result[key]=r.stdout.strip()
    paths={'serial':c['port'],**{k:v['path'] for k,v in c['cameras'].items()}}
    for role,device in paths.items():
        r=subprocess.run(['sudo','-n','fuser',device],capture_output=True,text=True,timeout=8)
        ok=allowed_owners(r.returncode,r.stdout,r.stderr,role=='ceiling_vertical',lambda pid:(Path('/proc')/pid/'cgroup').read_text())
        result['devices'][role]={'exists':Path(device).exists(),'owners_ok':ok and Path(device).exists(),'query_code':r.returncode}
    result['all_safe']=result['lelab']=='inactive' and result['astra']=='active' and all(v['owners_ok'] for v in result['devices'].values())
    return result


def require():
    result=inspect()
    if not result["all_safe"]:raise RuntimeError("장치 사전검사 거부: "+json.dumps(result,ensure_ascii=False))
    return result
