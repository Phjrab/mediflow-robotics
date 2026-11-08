"""운영자 전용 읽기 시간 계측. 기본은 계획 출력이며 모터 목표·토크 쓰기 없음."""
import argparse
import json
from pathlib import Path
import sys
import time
from first_trial_core import Hold,Limits,sensor_gate,frame_gate
from hold_continuation import HoldContinuation,latch_new_stop
from reviewed_start_stop import ReviewedStartStop
from run_receipts import finish_resources,json_value


def measure(io,*,clock=time.monotonic,sleep=time.sleep,steps=10):
    if steps!=10:raise Hold("계측은10회 고정")
    rows=[];start=clock();last=None
    for step in range(10):
        if clock()-start>5:raise Hold("읽기 계측5초 상한")
        row=dict(step=step)
        a=clock()
        if last is not None:sleep(max(0,last+1/30-clock()))
        row["sleep_seconds"]=clock()-a
        cycle_started=clock()
        a=clock()
        if step%5==0:
            frames=io.capture_frames();frame_gate(frames,clock(),Limits())
        row["camera_seconds"]=clock()-a
        a=clock();snapshot=io.snapshot();sensor_gate(snapshot,Limits(),starting=step==0)
        row["snapshot_seconds"]=clock()-a
        a=clock();io.check_records();row["records_seconds"]=clock()-a
        row["read_only_cycle_seconds"]=clock()-cycle_started
        row["marker_at"]=clock();row["state"]=snapshot["state"]
        row["goal_send_seconds"]=None
        rows.append(row);last=clock()
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute",action="store_true");p.add_argument("--check-files",action="store_true")
    p.add_argument("--operator-present",action="store_true");p.add_argument("--out",type=Path)
    args=p.parse_args(argv);root=Path(__file__).resolve().parent
    if not args.execute and not args.check_files:
        print("PLAN_ONLY: 읽기 계측 준비. 장치·추론·모터 쓰기 없음.");return 0
    if args.execute and (args.check_files or not args.operator_present or not sys.stdin.isatty()):raise Hold("운영자 터미널 직접 계측 필요")
    review=ReviewedStartStop(root,profile_success=True)
    receipt=HoldContinuation(root,Path.home()/"torque_guard_20261108_155436/events.jsonl",start_review=review)
    if args.check_files:
        print(json.dumps(dict(status="TIMING_FILES_READY",hardware_access=False,goal_writes_allowed=False)));return 0
    out=(args.out or Path.home()/("control_timing_"+str(time.time_ns()))).expanduser();out.mkdir(exist_ok=False)
    io=None;started=False;result=dict(status="HOLD",output=str(out).replace(str(Path.home()),"~"),motor_goal_packets=0,torque_register_writes=0)
    try:
        from trial_runtime import static_context,DeviceIO
        from operator_arm import confirm
        context=static_context();receipt.bind_context(context);receipt.confirm_operator(confirm);receipt.claim(out)
        io=DeviceIO(context,expected_torque=1,allow_goal=False,continuation=receipt);started=True
        io.connect();sensor_gate(io.snapshot(),Limits(),starting=True);io.connect_cameras()
        result["rows"]=measure(io);result["status"]="READ_ONLY_TIMING_COMPLETE"
    except BaseException as error:result["error"]=dict(kind=type(error).__name__,reason=str(error).replace(str(Path.home()),"~"),observed=getattr(error,"observed",None))
    finally:
        result.update(finish_resources(("io",io)))
        if result["errors"]:result["status"]="HOLD"
        if started and result["status"]=="HOLD":result["stop"]=latch_new_stop(root,result,start_review=review)
        with (out/"TIMING_RESULT.json").open("x",encoding="utf-8") as f:json.dump(json_value(result),f,ensure_ascii=False,allow_nan=False,indent=2)
        print(json.dumps({k:result[k] for k in ("status","output","motor_goal_packets","torque_register_writes","ports_closed")}))
    return 0 if result["status"]=="READ_ONLY_TIMING_COMPLETE" else 2


if __name__=="__main__":raise SystemExit(main())
