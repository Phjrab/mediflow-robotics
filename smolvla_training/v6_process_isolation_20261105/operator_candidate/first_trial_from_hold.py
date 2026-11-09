"""토크 유지 성공 기록에서 시작하는 운영자 전용 첫 동작. 기본은 계획 출력."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time
from first_trial_core import Hold,Limits,FirstMotionPlan,sensor_gate
from first_trial import qualified,SupervisedAdapter
from controller_bridge import load_controller
from hold_continuation import HoldContinuation,latch_new_stop,sha,STOP_SHA
from run_receipts import finish_resources,save_run,verify_target_receipt,json_value


def preflight():
    from trial_runtime import dependencies
    dependencies()
    import device_preflight
    device_preflight.require()


def main(argv=None):
    root=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument("--live",action="store_true")
    mode.add_argument("--check-files",action="store_true")
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--operator-present",action="store_true")
    parser.add_argument("--supported",action="store_true")
    parser.add_argument("--stop-ready",action="store_true")
    review_mode=parser.add_mutually_exclusive_group()
    review_mode.add_argument("--review-start-hold",action="store_true")
    review_mode.add_argument("--review-action-hold",action="store_true")
    review_mode.add_argument("--repeat-live",action="store_true")
    review_mode.add_argument("--quantized-repeat",action="store_true")
    review_mode.add_argument("--continue-clipped",action="store_true")
    review_mode.add_argument("--work-segment",action="store_true")
    review_mode.add_argument("--work-session",action="store_true")
    review_mode.add_argument("--data-stage",type=int,choices=(50,300))
    parser.add_argument("--motion-stats",type=Path)
    parser.add_argument("--servo-report",type=Path)
    parser.add_argument("--data-dry-receipt",type=Path)
    parser.add_argument("--data-live50-receipt",type=Path)
    parser.add_argument("--hold-log",type=Path,default=Path.home()/"torque_guard_20261108_155436/events.jsonl")
    parser.add_argument("--repeat-summary",type=Path,default=root.parent/"REPEAT_SUMMARY.json")
    parser.add_argument("--out",type=Path)
    parser.add_argument("--task",choices=["Put medicine bottle A into the "+c+" basket" for c in ("red","green","blue")],default="Put medicine bottle A into the blue basket")
    args=parser.parse_args(argv)
    if args.data_stage:
        if args.task != "Put medicine bottle A into the blue basket":
            parser.error("데이터 기반 시험은 약통 A와 파란 바구니 전용")
        if not all((args.motion_stats,args.servo_report,args.data_dry_receipt)):
            parser.error("데이터 통계, 서보 진단, dry-run 결과 파일 필요")
        args.motion_stats=args.motion_stats.expanduser().resolve(strict=True)
        args.servo_report=args.servo_report.expanduser().resolve(strict=True)
        args.data_dry_receipt=args.data_dry_receipt.expanduser().resolve(strict=True)
        if args.data_live50_receipt:
            args.data_live50_receipt=args.data_live50_receipt.expanduser().resolve(strict=True)
    def continuation():
        start_review=None
        if args.data_stage:
            from reviewed_start_stop import ReviewedStartStop
            start_review=ReviewedStartStop.for_data_motion(root,args.data_stage,args.motion_stats,args.servo_report,
                                                           args.data_dry_receipt,args.data_live50_receipt)
        elif args.review_start_hold or args.review_action_hold or args.repeat_live or args.quantized_repeat or args.continue_clipped or args.work_segment or args.work_session:
            from reviewed_start_stop import ReviewedStartStop
            start_review=ReviewedStartStop(root,review_action=args.review_action_hold,repeat_live=args.repeat_live,quantized_repeat=args.quantized_repeat,continue_clipped=args.continue_clipped,work_segment=args.work_segment,work_session=args.work_session)
        return HoldContinuation(root,args.hold_log,start_review=start_review)
    if not args.execute:
        if args.check_files:
            receipt=continuation();qualified(args.repeat_summary)
            print(json.dumps(dict(status="HOLD_FILES_READY",**receipt.summary,hardware_access=False,live_authorized=False),ensure_ascii=False))
        else:
            token=f"REARM_DATA_{args.data_stage}" if args.data_stage else "REARM_SESSION" if args.work_session else "REARM_WORK" if args.work_segment else "REARM_CONTINUE" if args.continue_clipped else "REARM_QUANTIZED" if args.quantized_repeat else "REARM_REPEAT" if args.repeat_live else "REARM_ACTION" if args.review_action_hold else "REARM_START" if args.review_start_hold else "REARM_HOLD"
            print("PLAN_ONLY: 토크 변경·장치·추론·목표 쓰기 없음. 새 "+token+"와 LIVE 직접 확인이 필요합니다.")
        return 0
    if not args.live or args.check_files or not all((args.operator_present,args.supported,args.stop_ready)) or not sys.stdin.isatty():
        raise Hold("현장 직접 live·execute·팔 지지·정지 수단 확인 필요; 장치 미연결")
    output=(args.out or Path.home()/("first_vla_from_hold_"+str(time.time_ns()))).expanduser()
    output.mkdir(parents=True,exist_ok=False)
    limits=Limits()
    if args.work_segment or args.work_session or args.data_stage:
        from work_segment_limits import WorkSegmentLimits
        limits=WorkSegmentLimits();limits.validate()
    result=dict(status="HOLD",mode="work_segment" if args.work_segment else "live_from_hold",first_trial_only=True,task=args.task,settings=limits.__dict__,
                output=str(output).replace(str(Path.home()),"~"),torque_register_writes=0)
    if args.work_session:result.update(mode="work_session",session_budget=dict(max_chunks=6,max_steps=300,max_seconds=30))
    if args.data_stage:result.update(mode=f"data_live_{args.data_stage}")
    io=probe=policy=adapter=receipt=None;hardware_started=False
    try:
        receipt=continuation();qualified(args.repeat_summary)
        from trial_runtime import static_context,DeviceIO,InspectionAdapter,PolicyProvider
        from operator_arm import confirm
        from bounded_wait import wait
        from runtime_cpu import configure_main
        context=static_context();receipt.bind_context(context);Controller=load_controller()
        result.update(evidence_files=context["files"],camera_mapping=context["mapping"],hold_evidence=receipt.summary)
        io_options={}
        if args.data_stage:
            from data_motion_evidence import inputs, digest
            rates,current_limits,_=inputs(args.motion_stats,args.servo_report)
            io_options["current_limits"]=current_limits
            result["data_evidence"]=dict(stats=digest(args.motion_stats),servo=digest(args.servo_report),dry=digest(args.data_dry_receipt))
            result["data_speed_caps"]=rates
        preflight()
        configure_main(output/"CPU_RUNTIME.json")
        print("모델 로딩·예열 중: serial·카메라는 닫혀 있고 모터 명령은 없습니다. 완료 후 현재 상태를 다시 읽습니다.",flush=True)
        policy=PolicyProvider(context,args.task)
        preflight();receipt.confirm_operator(confirm);receipt.claim(output)
        probe=DeviceIO(context,expected_torque=1,allow_goal=False,continuation=receipt,**io_options)
        hardware_started=True
        inspection=Controller(InspectionAdapter(probe)).read_status()
        result["inspection"]=inspection
        sensor_gate(inspection["snapshot"],Limits(),starting=True)
        if inspection.get("arm_ready") is not True:
            error=Hold("시작 위치·목표 대조 실패; 상세 start_agreement 확인")
            error.observed=inspection.get("repeat_continuity") or inspection.get("start_agreement",inspection)
            raise error
        io=DeviceIO(context,expected_torque=1,allow_goal=True,policy=policy,continuation=receipt,**io_options)
        evidence=dict(kind="OPERATOR_ARMED",model_verified=True,units_verified=True,mapping_verified=True,
                      operator_live_confirmed=False,arm_receipt_verified=True)
        Adapter=SupervisedAdapter
        if args.data_stage:
            from data_motion_stage import DataMotionAdapter, DataMotionSession
            Adapter=DataMotionSession if args.data_stage==300 else DataMotionAdapter
        elif args.work_session:
            from work_session import WorkSessionAdapter
            Adapter=WorkSessionAdapter
        adapter=Adapter(io,evidence,context["home"],[],waiter=wait,limits=limits);adapter.output=output
        if args.data_stage:
            adapter.configure_data_motion(rates,inspection["target_feedback_raw"])
        adapter.compensated=args.repeat_live or args.quantized_repeat or args.continue_clipped or args.work_segment or args.work_session
        adapter.quantized=args.quantized_repeat or args.continue_clipped or args.work_segment or args.work_session
        adapter.clip_warning_only=args.continue_clipped or args.work_segment or args.work_session
        Controller(adapter).run(FirstMotionPlan(True))
        expected=args.data_stage or (sum(c["sent_count"] for c in adapter.session_chunks) if args.work_session else limits.max_steps)
        verify_target_receipt(io,adapter.sent,expected)
        result["status"]=f"DATA_LIVE_{args.data_stage}_COMPLETE" if args.data_stage else "WORK_SESSION_COMPLETE" if args.work_session else "WORK_SEGMENT_COMPLETE" if args.work_segment else "FIRST_TRIAL_COMPLETE"
    except BaseException as error:
        result["error"]=dict(kind=type(error).__name__,reason=str(error).replace(str(Path.home()),"~"),
                             observed=getattr(error,"observed",None) or getattr(io,"last_fault",None),
                             initialization_cleanup_error=getattr(error,"initialization_cleanup_error",None))
    finally:
        cleanup=finish_resources(("io",io),("probe",probe),("policy",policy))
        result.update(cleanup_errors=cleanup["errors"],ports_closed=cleanup["ports_closed"],
                      motor_goal_packets=io.permission.goal_packets if io is not None and io.permission is not None else 0,
                      hardware_started=hardware_started)
        if io is not None and hasattr(io,'goal_telemetry'):
            try:
                with (output/'goal_telemetry.json').open('x',encoding='utf-8') as stream:
                    json.dump(json_value(io.goal_telemetry.rows),stream,ensure_ascii=False,allow_nan=False,indent=2)
                result['goal_telemetry_count']=len(io.goal_telemetry.rows)
            except BaseException as error:result['cleanup_errors'].append(dict(component='goal_telemetry',kind=type(error).__name__))
        if adapter is not None:
            if args.data_stage:
                result["data_steps"]=adapter.data_steps
                if adapter.tracking is not None:
                    result["tracking_ratios"]=adapter.tracking.ratios()
                    result["last_wire_target"]=adapter.tracking.previous
            if hasattr(adapter,"session_chunks"):
                result.update(completed_chunks=sum(c["status"]=="COMPLETE" for c in adapter.session_chunks),session_end_reason=getattr(adapter,"session_end_reason",None))
                try:
                    with (output/"session_chunks.json").open("x",encoding="utf-8") as stream:json.dump(json_value(adapter.session_chunks),stream,ensure_ascii=False,indent=2)
                except BaseException as error:result["cleanup_errors"].append(dict(component="session_chunks",kind=type(error).__name__))
            if hasattr(adapter,"step_timings"):
                try:
                    with (output/"step_timings.json").open("x",encoding="utf-8") as stream:json.dump(json_value(adapter.step_timings),stream,ensure_ascii=False,indent=2)
                except BaseException as error:result["cleanup_errors"].append(dict(component="step_timings",kind=type(error).__name__))
            result.update(targets=adapter.sent,events=adapter.events)
            if hasattr(adapter,"policy_chunk"):
                try:
                    with (output/"policy_chunk.json").open("x",encoding="utf-8") as stream:
                        json.dump(json_value(adapter.policy_chunk),stream,ensure_ascii=False,allow_nan=False,indent=2)
                    result["policy_chunk_saved"]=True
                except BaseException as error:result["cleanup_errors"].append(dict(component="policy_chunk",kind=type(error).__name__))
            if hasattr(adapter,"last_target_context"):result["last_target_context"]=adapter.last_target_context
            try:
                with (output/"targets.csv").open("x",encoding="utf-8",newline="") as stream:
                    writer=csv.DictWriter(stream,fieldnames=("step","raw_action","target","state","clip","sent_at"));writer.writeheader();writer.writerows(adapter.sent)
            except BaseException as error:result["cleanup_errors"].append(dict(component="targets_csv",kind=type(error).__name__))
        try:result["original_stop_preserved"]=sha(root/"FIELD_STOP.json")==STOP_SHA
        except OSError:result["original_stop_preserved"]=False
        if result["cleanup_errors"] or not result["original_stop_preserved"]:result["status"]="HOLD"
        if hardware_started and result["status"]=="HOLD":
            try:result["new_stop_receipt"]=latch_new_stop(root,result,start_review=getattr(receipt,"start_review",None))
            except OSError as error:result["new_stop_receipt"]=dict(created=False,error_kind=type(error).__name__)
        try:result=save_run(root,output,result,latch=False)
        except OSError as error:
            result.update(status="HOLD",result_save_error=type(error).__name__)
        summary={key:result[key] for key in ("status","mode","first_trial_only","output","motor_goal_packets","torque_register_writes","original_stop_preserved")}
        summary["steps"]=len(result.get("targets",[]));summary["ports_closed"]=result["ports_closed"]
        if "error" in result:summary["error"]={key:result["error"][key] for key in ("kind","reason")}
        if args.data_stage:summary["tracking_ratios"]=result.get("tracking_ratios")
        if "new_stop_receipt" in result:summary["new_stop_receipt"]=result["new_stop_receipt"]
        if "result_save_error" in result:summary["result_save_error"]=result["result_save_error"]
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if result["status"] in ("FIRST_TRIAL_COMPLETE","WORK_SEGMENT_COMPLETE","WORK_SESSION_COMPLETE",
                                     "DATA_LIVE_50_COMPLETE","DATA_LIVE_300_COMPLETE") else 2


if __name__=="__main__":raise SystemExit(main())
