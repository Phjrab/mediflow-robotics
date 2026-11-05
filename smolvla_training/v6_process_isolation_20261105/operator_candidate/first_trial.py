#!/usr/bin/env python3
"""기본 계획 출력. --inspect만 읽기 전용. 실기는 운영자의 현장 입력을 요구한다."""
import argparse
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from first_trial_core import Hold,FirstTrialAdapter,FirstMotionPlan,Limits,CAMERAS
from controller_bridge import load_controller

def qualified(path):
    if path is None:raise Hold("반복 dry-run 검증 파일 없음")
    report=json.loads(Path(path).expanduser().read_text())
    if report.get("all_pass") is not True or report.get("planned")!=3 or report.get("completed")!=3 or report.get("passed")!=3:
        raise Hold("반복 dry-run 3/3 통과 필요; 지연 기준 유지")
    cases=report.get("cases",[])
    if len(cases)!=3:raise Hold("반복 검증 항목 누락")
    for row in cases:
        if row.get("status")!="V6_FAST_DRY_COMPLETE" or row.get("register_writes")!=0 or row.get("post_checks") is not True or row.get("record_preserved") is not True:
            raise Hold("반복 검증 근거 불충족")
    return report

class SupervisedAdapter(FirstTrialAdapter):
    def connect(self):
        super().connect();self.bounds=self.io.joint_bounds();self.io.connect_cameras()
    def prepare(self):
        from operator_arm import confirm
        from PIL import Image
        frames=self.await_job(self.io.capture_frames,time.monotonic()+2,1.)
        from first_trial_core import frame_gate
        frame_gate(frames,time.monotonic(),self.limits)
        for name in CAMERAS:Image.fromarray(frames[name]["pixels"]).save(self.output/(name+"_start.png"))
        confirm("LIVE","첫 동작 시험: 최대10스텝/5초, 정상 완료도 약통 투입 성공 판정이 아닙니다.",monitor=self.monitor)
        self.evidence["operator_live_confirmed"]=True
        super().prepare();self.io.authorized=True

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument("--inspect",action="store_true")
    mode.add_argument("--live",action="store_true")
    mode.add_argument("--release",action="store_true")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--operator-present",action="store_true")
    p.add_argument("--supported",action="store_true")
    p.add_argument("--stop-ready",action="store_true")
    p.add_argument("--repeat-summary",type=Path)
    p.add_argument("--task",default="Put medicine bottle A into the blue basket",choices=["Put medicine bottle A into the "+c+" basket" for c in ("red","green","blue")])
    p.add_argument("--out",type=Path)
    return p

def main(argv=None):
    args=parser().parse_args(argv)
    if not args.inspect and not args.execute:
        print("PLAN_ONLY: 장치·추론·모터 쓰기 없음. 지연 검증 실패 상태에서는 live 실행 불가.");return 0
    if args.execute and not (args.live or args.release):raise Hold("명시적인 live/release 모드 필요")
    if args.live or args.release:
        if args.live and (Path(__file__).parent/"FIELD_STOP.json").exists():raise Hold("현장 중단 기록 미검토; 삭제/우회 없이 중단")
        if not all((args.execute,args.operator_present,args.supported,args.stop_ready)):raise Hold("현장 입회·팔 지지·정지 수단·execute 확인 필요")
        if args.live:qualified(args.repeat_summary)
        if not sys.stdin.isatty():raise Hold("운영자 터미널 직접 실행 필요")
    if args.out is None:raise Hold("새 --out 경로 필요")
    output=args.out.expanduser();output.mkdir(parents=True,exist_ok=False)
    from trial_runtime import static_context,DeviceIO,InspectionAdapter,PolicyProvider,dependencies
    context=static_context();Controller=load_controller()
    result={"mode":"inspect" if args.inspect else "release" if args.release else "live","status":"HOLD","first_trial_only":True,
            "task":args.task,"settings":Limits().__dict__,"evidence_files":context["files"],"camera_mapping":context["mapping"]}
    io=None;policy=None;adapter=None;arm=None
    try:
        if args.inspect:
            io=DeviceIO(context,expected_torque=0)
            result["inspection"]=Controller(InspectionAdapter(io)).read_status()
            result["status"]="READ_ONLY_COMPLETE"
        else:
            from operator_arm import ArmingAdapter,confirm
            from bounded_wait import wait
            def emit(row):
                with (output/"operator_events.jsonl").open("a",encoding="utf-8") as f:f.write(json.dumps(dict(unix=time.time(),**row),ensure_ascii=False)+"\n")
            if args.live:
                # GPU 예열은 토크OFF 읽기 확인 후 진행한다. 예열 중 포트는 닫혀 있다.
                probe=DeviceIO(context,expected_torque=0)
                readiness=Controller(InspectionAdapter(probe)).read_status()
                if not readiness["arm_ready"]:raise Hold("읽기 점검에서 목표/현재 불일치 또는 보정 범위 이탈")
                from runtime_cpu import configure_main
                configure_main(output/"CPU_RUNTIME.json")
                policy=PolicyProvider(context,args.task)
                confirm("REARM","원래 토크ON 중단 기록은 보존되어 있습니다. 현장에서 중단 사유를 검토하고 재무장할 때만 진행하세요.")
            arm=ArmingAdapter(context,emit,release=args.release)
            Controller(arm).run(FirstMotionPlan(True,stages=("manual_torque",)))
            result["operator_arm"]=arm.result
            if args.live:
                io=DeviceIO(context,expected_torque=1,allow_goal=True,policy=policy)
                evidence=dict(kind="OPERATOR_ARMED",model_verified=True,units_verified=True,mapping_verified=True,
                              operator_live_confirmed=False,arm_receipt_verified=arm.result is not None)
                adapter=SupervisedAdapter(io,evidence,context["home"],[],waiter=wait)
                adapter.output=output
                Controller(adapter).run(FirstMotionPlan(True))
                result["targets"]=adapter.sent
            result["status"]="FIRST_TRIAL_COMPLETE" if args.live else "TORQUE_OFF_SUPPORTED"
    except BaseException as error:
        result["error"]={"kind":type(error).__name__,"reason":str(error).replace(str(Path.home()),"~")}
        print(json.dumps(result["error"],ensure_ascii=False))
    finally:
        if io is not None:io.close()
        if policy is not None:policy.close()
        result["motor_goal_packets"]=io.permission.goal_packets if io is not None and io.permission is not None else 0
        result["cleanup_errors"]=io.cleanup_errors if io is not None else []
        result["stop_inventory"]=context["stop_inventory"]
        if adapter is not None:
            result["events"]=adapter.events
            with (output/"targets.csv").open("x",encoding="utf-8",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=("step","raw_action","target","state","clip","sent_at"));writer.writeheader();writer.writerows(adapter.sent)
        with (output/"RESULT.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False))
    return 0 if result["status"]!="HOLD" else 2

if __name__=="__main__":raise SystemExit(main())
