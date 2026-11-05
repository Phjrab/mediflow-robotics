"""개별/동기 상태 읽기 실제 대조. 토크ON/45도 이상이면 즉시 추가 읽기 중단."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import time
from latency_dependencies import prepare
from batch_motor_read import torque_modes,health,JOINTS

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--out",type=Path,required=True);args=p.parse_args()
    if args.out.exists():raise RuntimeError("진단 출력 이미 존재")
    prepare()
    import device_preflight as devices
    import probe_current_state as probe
    from live_safety_runtime import PacketPermission,close_without_writes
    devices.require()
    if probe.digest(probe.CALIBRATION)!=probe.CALIBRATION_HASH:raise RuntimeError("보정 해시 불일치")
    from lerobot.robots.so_follower import SO101Follower,SO101FollowerConfig
    robot=SO101Follower(SO101FollowerConfig(port=probe.PORT,id="so-101",calibration_dir=probe.CALIBRATION.parent,
                 cameras={},use_degrees=True,disable_torque_on_disconnect=False))
    signature=inspect.signature(robot.bus.sync_read)
    if not {"normalize","num_retry"}.issubset(signature.parameters):raise RuntimeError("sync_read API 미확인")
    permission=PacketPermission(robot.bus.port_handler).install();result={"status":"HOLD"}
    try:
        robot.bus.connect(handshake=False)
        import fcntl,termios
        fcntl.ioctl(robot.bus.port_handler.ser.fileno(),termios.TIOCEXCL)
        t=time.perf_counter();individual={}
        for register in ("Torque_Enable","Present_Temperature","Operating_Mode","Present_Load","Status"):
            values={}
            for joint in JOINTS:
                v=robot.bus.read(register,joint,normalize=False,num_retry=0);values[joint]=v
                if register in ("Torque_Enable","Operating_Mode","Status") and (type(v) is not int or v!=0):raise ValueError("개별 읽기 비정상/토크ON")
                if register=="Present_Temperature" and not 0<=v<45:raise ValueError("개별 읽기45도 이상")
                if register=="Present_Load" and abs(v)>=200:raise ValueError("개별 읽기 과부하")
            individual[register]=values
        individual_seconds=time.perf_counter()-t
        t=time.perf_counter();torque,modes=torque_modes(robot.bus);temps=health(robot.bus);batch_seconds=time.perf_counter()-t
        if torque!=individual["Torque_Enable"] or modes!=individual["Operating_Mode"]:raise ValueError("RAW 읽기 값 불일치")
        result.update(status="BATCH_READ_API_CONFIRMED",signature=str(signature),individual_seconds=individual_seconds,
                      batch_seconds=batch_seconds,torque=torque,modes=modes,temperatures=temps,
                      temperature_delta={j:temps[j]-individual["Present_Temperature"][j] for j in JOINTS})
    except Exception as error:result["error"]={"kind":type(error).__name__,"reason":str(error).replace(str(Path.home()),"~")}
    finally:
        result["cleanup_errors"]=close_without_writes(robot,[],True,permission)
        result.update(read_packets=permission.read_packets,goal_packets=permission.goal_packets,blocked_packets=permission.blocked_packets)
        result["owners_after"]=probe.owners()
        with args.out.open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result["status"]=="BATCH_READ_API_CONFIRMED" and result["owners_after"]["free"] and not result["cleanup_errors"] else 2

if __name__=="__main__":raise SystemExit(main())
