"""이미 serial 중단된 세션의 종료 OS 상태만 확인한다. serial을 열지 않는다."""
import csv
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from artifacts import save_json,save_csv,temperatures

ROOT=Path(__file__).resolve().parent
def status(unit,user=False):
    p=subprocess.run(['systemctl']+(['--user'] if user else [])+['is-active',unit],capture_output=True,text=True)
    return dict(exit_code=p.returncode,state=p.stdout.strip())
def main():
    stop=ROOT/'SERIAL_STOP.json'
    last=json.loads(stop.read_text()) if stop.exists() else 'UNVERIFIED_MISSING_STOP_FILE'
    paths=['/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00',
        '/dev/v4l/by-id/usb-Generic_USB_Camera_200901010001-video-index0',
        '/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435_236223023645-video-index0']
    p=subprocess.run(['sudo','-n','fuser','-v',*paths],capture_output=True,text=True)
    free=p.returncode==1 and not p.stdout.strip() and not p.stderr.strip()
    names={'home_align_web.py','dry_inference.py','evaluate_offline.py','prepare_samples.py','capture_cameras.py','torque_so101.py'}
    active=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid():continue
        try:
            if proc.stat().st_uid!=os.getuid():continue
            argv=(proc/'cmdline').read_bytes().split(b'\x00')
            found=[Path(x.decode(errors='replace')).name for x in argv if x and Path(x.decode(errors='replace')).name in names]
            if found:active.append(dict(pid=int(proc.name),scripts=found))
        except (OSError,ValueError):continue
    sock=socket.socket();sock.settimeout(.3)
    try:monitor_listening=sock.connect_ex(('127.0.0.1',8765))==0
    finally:sock.close()
    result=dict(time=time.time(),serial_final_read='NOT_RUN_STOP_CONDITION_HAS_PRIORITY',last_serial_observation=last,
        unobserved_motor_values='UNVERIFIED',register_writes=0,port_free=free,fuser_exit_code=p.returncode,
        fuser_has_output=bool(p.stdout.strip() or p.stderr.strip()),active_task_processes=active,
        monitor_8765_listening=monitor_listening,astra_bridge=status('astra-v4l2-bridge.service'),
        lelab=status('lelab-jetson-py310.service',True),jetson_temperatures=temperatures())
    save_json(ROOT/'reports/FINAL_SYSTEM_STATUS.json',result)
    backups=[dict(path=str(path.relative_to(ROOT)),bytes=path.stat().st_size) for path in sorted(ROOT.rglob('*.bak_*')) if path.is_file()]
    save_csv(ROOT/'reports/BACKUPS.csv',backups)
    print(json.dumps(dict(port_free=free,active_task_processes=active,monitor_8765_listening=monitor_listening,
        astra_bridge=result['astra_bridge'],last_serial_observation=last,backup_files=len(backups)),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
