"""기존 controller로 개별/동기 목표 읽기를 비교. 카메라·모델 추론·모터 쓰기 없음."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")

def crosscheck(io,context):
    initial=io.snapshot()
    from first_trial_core import home_gate
    home_gate(initial["state"],context["home"])
    if initial["moving"]:raise RuntimeError("움직임 감지; 추가 읽기 중단")
    grouped=io.raw("Goal_Position");rows=[];bus=io.robot.bus
    for joint in JOINTS:
        health=io.health_snapshot()
        present=bus.read("Present_Position",joint,normalize=False,num_retry=0)
        goal=bus.read("Goal_Position",joint,normalize=False,num_retry=0)
        moving=bus.read("Moving",joint,normalize=False,num_retry=0)
        if type(moving) is not int or moving!=0:raise RuntimeError("움직임/응답 이상; 추가 읽기 중단")
        cal=bus.calibration[joint]
        rows.append(dict(joint=joint,present_raw=present,goal_individual_raw=goal,goal_sync_raw=grouped[joint],
                         calibration_raw_range=[cal.range_min,cal.range_max],torque=health["torque"][joint],mode=health["modes"][joint],
                         temperature=health["temperatures"][joint],load=health["loads"][joint],status=health["statuses"][joint],moving=moving))
    last=io.health_snapshot();io.check_records()
    return dict(status="GOAL_READ_CROSSCHECK_COMPLETE",joints=rows,
                calibration_sha256=hashlib.sha256(context["calibration"].read_bytes()).hexdigest(),
                register_writes=io.permission.goal_packets,read_packets=io.permission.read_packets,blocked_packets=io.permission.blocked_packets,
                records_preserved=True,final_torque=last["torque"],final_temperatures=last["temperatures"],unix_end=time.time())

def main():
    p=argparse.ArgumentParser();p.add_argument("--code-root",type=Path,default=Path.home()/"first_trial_operator_1793885625046_final");p.add_argument("--out",type=Path,required=True);args=p.parse_args()
    if args.out.exists():raise RuntimeError("기존 출력 덮어쓰기 거부")
    root=args.code_root.expanduser();manifest=json.loads((Path(__file__).parent/"REVIEWED_READONLY_CODE.json").read_text(encoding="utf-8"))
    for name,digest in manifest.items():
        if hashlib.sha256((root/name).read_text(encoding="utf-8").encode()).hexdigest()!=digest:raise RuntimeError("읽기 코드 해시 불일치: "+name)
    sys.path.insert(0,str(root))
    from trial_runtime import static_context,DeviceIO,check_context
    from controller_bridge import load_controller
    context=static_context();io=DeviceIO(context,expected_torque=0,allow_goal=False)
    class Adapter:
        def connect(self):io.connect()
        def read_status(self):return crosscheck(io,context)
        def close(self):io.close()
    try:
        report=load_controller()(Adapter()).read_status()
        check_context(context)
    except BaseException as e:
        report=dict(status="GOAL_CROSSCHECK_HOLD",kind=type(e).__name__,reason=str(e).replace(str(Path.home()),"~"),register_writes=io.permission.goal_packets if io.permission else 0)
    report["cleanup_errors"]=io.cleanup_errors
    if io.cleanup_errors:report["status"]="GOAL_CROSSCHECK_HOLD"
    with args.out.open("x",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status=report["status"],register_writes=report["register_writes"],goal_individual=[r["goal_individual_raw"] for r in report.get("joints",[])],goal_sync=[r["goal_sync_raw"] for r in report.get("joints",[])],cleanup_errors=io.cleanup_errors),ensure_ascii=False))
    return 0 if report["status"]=="GOAL_READ_CROSSCHECK_COMPLETE" else 2

if __name__=="__main__":raise SystemExit(main())
