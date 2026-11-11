"""기존 v6를 보존하고 확인서가 필요한 읽기 전용 변형을 새 파일에 생성."""
import ast
import hashlib
from pathlib import Path

root=Path(__file__).resolve().parent
source=root/"so101_first_action_test_safe_v6.py"
if hashlib.sha256(source.read_bytes()).hexdigest()!="9e790433490299d57124a1861139171fa27668d18deae4c00ffd72344b415ec5":
    raise SystemExit("검토한 v6 원본 해시 불일치")
text=source.read_text(encoding="utf-8")
def replace(old,new):
    global text
    if text.count(old)!=1:raise RuntimeError("변경 근거가 유일하지 않음: "+old[:60])
    text=text.replace(old,new)
def function(name, body):
    global text
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=text.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[body+"\n"]
    text="".join(lines)

replace('                                 monitored_inference, close_without_writes)',
'''                                 monitored_inference, close_without_writes,
                                 finish_workers_after_port_close)
from dry_session import DrySession, home_path
from checkpoint_amp import saved_amp, infer_with_saved_amp
from persistent_policy_worker import PersistentPolicyWorker''')
replace('    parser = argparse.ArgumentParser(description=__doc__)', '''    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-session",type=Path,required=True,help="1회 읽기 전용 재개 확인서")''')
for default in ('"/dev/v4l/by-id/usb-Generic_USB_Camera_200901010001-video-index0"',
                '"/dev/video8"','"/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435_236223023645-video-index0"'):
    replace('default='+default+', help=', 'default=None, help=')
replace('''    # 기존 무인 중단 기록을 자동 해제/무시하지 않는다. 모델/장치 import보다 먼저 거부.
    check_stop_record()''','''    # 과거 중단 사유/해시와 새 진단에 묶인 1회 읽기 재개. live는 허가하지 않는다.
    session=DrySession(args.dry_session,live=args.live)
    session.begin(args)
    import device_preflight
    device_preflight.require()''')
replace('if args.live and metadata["checkpoint_kind"]', 'if metadata["checkpoint_kind"]')
replace('''    print(json.dumps(metadata, ensure_ascii=False, indent=2))''', '''    print(json.dumps({k:metadata[k] for k in ("checkpoint_kind","camera_mapping","normalization_files")}, ensure_ascii=False))''')
replace('''    if args.live:
        verify_units(args.unit_evidence, checkpoint, calibration_file)''', '''    if args.live or args.dry_session:
        verify_units(args.unit_evidence, checkpoint, calibration_file)''')
replace('''    if args.live and (not home or''','''    if (not home or''')
replace('''allow_goal=args.live''','''allow_goal=False''')
replace('''    def monitor():
        check_stop_record()
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, args.live)
        read_health(robot.bus, load_limit=args.load_limit)''','''    def monitor():
        session.check()
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, False)
        read_health(robot.bus, load_limit=args.load_limit)
        session.monitor_count += 1''')
replace('''    csv_file = None
    try:''','''    csv_file = None
    run_error = None
    phase_metrics=[]
    try:''')
replace('''        print("관절 버스 연결 및 보정 읽기 시작", flush=True)''','''        device_preflight.require()
        print("관절 버스 연결 및 보정 읽기 시작", flush=True)''')
replace('''        if args.live:
            home_gate(state, home)
        elif not home:''','''        if args.live or args.dry_session:
            home_gate(state, home)
        elif not home:''')
replace('''        csv_path = output / "chunks.csv"''','''        with (output/"state_start.json").open("x",encoding="utf-8") as f:
            json.dump(dict(joint_names=list(JOINTS),state=state,temperatures=temperatures,
                           torque=torque,modes=modes,source="CURRENT_READ_ONLY"),f,ensure_ascii=False,indent=2)
        csv_path = output / "chunks.csv"''')
replace('''            state, packed, _ = observed''','''            state, packed, _ = observed
            home_gate(state,home)
            with (output/("state_inference_%d.json"%chunk_count)).open("x",encoding="utf-8") as f:
                json.dump(dict(state=state,task=args.task),f,ensure_ascii=False)''')
replace('''            observed = build_observation(robot, torch, np)''','''            capture_start=time.perf_counter()
            observed = build_observation(robot, torch, np)
            phase_metrics.append(dict(capture_seconds=time.perf_counter()-capture_start))''')
replace('''            actions = monitored_inference(predict_only, monitor,''','''            phase_metrics[-1]["remaining_before_inference"]=remaining
            phase_metrics[-1]["inference_wait_budget"]=min(remaining,50/args.fps)
            actions = monitored_inference(lambda:gpu_worker.call(predict_only,label="current_observation_chunk"), monitor,''')
replace('''    devices = dict(end_effector=args.end_effector_camera,''','''    saved_model_config=json.loads((checkpoint/"config.json").read_text())
    if getattr(policy.config,"use_amp",None) is not saved_amp(saved_model_config):
        raise Hold("저장 AMP 설정과 로드된 정책 설정 불일치")
    import observation_contract as contract_api
    contract=contract_api.load_contract(home_path(session.data["files"]["camera_config"]["path"]))
    warm_frames={name:np.zeros((480,640,3),dtype=np.uint8) for name in CAMERAS}
    warm_observation=contract_api.pack_observation(warm_frames,home["state"],args.task,torch,contract)
    warm_times=[]
    gpu_worker=PersistentPolicyWorker()
    for _ in range(2):
        t=time.perf_counter()
        gpu_worker.call(lambda:infer_with_saved_amp(lambda:infer_chunk(policy,preprocess,postprocess,warm_observation,torch),torch,saved_model_config),label="synthetic_warmup")
        torch.cuda.synchronize();warm_times.append(time.perf_counter()-t)
    with (Path(__file__).resolve().parent/"warmup_measurements.json").open("x",encoding="utf-8") as f:
        json.dump(dict(synthetic_input=True,used_for_control=False,serial_open=False,seconds=warm_times,
                       saved_use_amp=saved_model_config["use_amp"],autocast_dtype=str(torch.get_autocast_dtype("cuda"))),f,indent=2)
    if not callable(getattr(policy,"reset",None)):
        raise Hold("합성 예열 후 정책 상태 초기화 API 미확인")
    gpu_worker.call(policy.reset,label="reset_after_warmup")
    del warm_frames,warm_observation
    devices = dict(end_effector=args.end_effector_camera,''')
replace('''                result = infer_chunk(policy, preprocess, postprocess, dict(packed, task=[args.task]), torch)''','''                result = infer_with_saved_amp(
                    lambda:infer_chunk(policy,preprocess,postprocess,dict(packed,task=[args.task]),torch),torch,saved_model_config)''')
replace('''            if args.live:
                first_action_gate(first, state, args.first_action_limit)''','''            first_action_gate(first, state, args.first_action_limit)''')
replace('''    except KeyboardInterrupt:
        print("Ctrl+C 감지. 추가 목표/토크 쓰기 없이 포트를 닫습니다.")
    except Exception:
        traceback.print_exc()
        raise''','''    except KeyboardInterrupt:
        run_error=dict(kind="KeyboardInterrupt",reason="Ctrl+C; 추가 목표/토크 쓰기 없음")
        raise SystemExit(130)
    except Exception as exc:
        run_error=dict(kind=type(exc).__name__,reason=str(exc).replace(str(Path.home()),"~"))
        if phase_metrics and "infer_started" in locals():phase_metrics[-1]["wait_elapsed_until_error"]=time.perf_counter()-infer_started
        print(json.dumps(dict(status="HOLD",error=run_error),ensure_ascii=False),flush=True)
        raise SystemExit(2)''')
replace('''        close_session(robot, connected_cameras, args.live, action_attempted, connected_bus)
        if csv_file is not None:
            csv_file.close()''','''        cleanup_errors=close_session(robot, connected_cameras, args.live, action_attempted, connected_bus)
        # serial을 닫은 뒤에만 이미 시작된 GPU 작업 종료를 기다린다. 결과는 재사용하지 않는다.
        pending=finish_workers_after_port_close(10)
        if not gpu_worker.close(10):pending+=1
        if csv_file is not None:
            csv_file.close()
        summary=dict(status="READ_ONLY_DRY_COMPLETE" if chunk_count==1 and run_error is None and not cleanup_errors and pending==0 else "HOLD",
                     error=run_error,chunks=chunk_count,monitor_count=session.monitor_count,
                     read_packets=permission.read_packets,goal_packets=permission.goal_packets,
                     blocked_packets=permission.blocked_packets,register_writes=permission.goal_packets,
                     cleanup_errors=cleanup_errors,pending_policy_threads=pending,live_permitted=False,
                     receipt_sha256=session.digest,record_inventory=session.data["stop_inventory"],
                     weights_converted=False,inference_use_amp=saved_model_config["use_amp"],
                     inference_autocast_dtype=str(torch.get_autocast_dtype("cuda")),
                     inference_settings_source="checkpoint.config.json",chunk_timeout_seconds=50/args.fps,
                     gpu_worker_history=gpu_worker.history,phase_metrics=phase_metrics)
        with (output/"DRY_RESULT.json").open("x",encoding="utf-8") as f:json.dump(summary,f,ensure_ascii=False,indent=2)
        print(json.dumps(summary,ensure_ascii=False),flush=True)''')
replace('''    for error in errors:
        print(error, file=sys.stderr)''','''    for error in errors:
        print(error, file=sys.stderr)
    return errors''')
replace('''                "output": str(output),''','''                "output": str(output).replace(str(Path.home()),"~"),''')
replace('''output))


if __name__''','''str(output).replace(str(Path.home()),"~")))


if __name__''')
function("read_motor_state",'''def read_motor_state(bus):
    torque,modes={},{}
    for joint in JOINTS:
        value=bus.read("Torque_Enable",joint,normalize=False,num_retry=0)
        torque[joint]=value
        if type(value) is not int or value!=0:raise Hold("dry-run 중 토크ON/미확인 감지; 추가 serial 읽기 중단")
    for joint in JOINTS:
        value=bus.read("Operating_Mode",joint,normalize=False,num_retry=0)
        modes[joint]=value
        if type(value) is not int or value!=0:raise Hold("dry-run 위치 모드0 아님")
    return torque,modes''')
function("read_health",'''def read_health(bus,starting=False,load_limit=200.0):
    temps={}
    for joint in JOINTS:
        t=bus.read("Present_Temperature",joint,normalize=False,num_retry=0)
        temps[joint]=t
        if type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<45:
            raise Hold("dry-run 45도 이상/미확인; 추가 serial 읽기 중단")
    loads={j:float(bus.read("Present_Load",j,normalize=False,num_retry=0)) for j in JOINTS}
    status={j:int(bus.read("Status",j,normalize=False,num_retry=0)) for j in JOINTS}
    health_gate(temps,loads,status,starting,load_limit)
    return temps''')
ast.parse(text,feature_version=(3,10))
with (root/"so101_first_action_test_safe_v6_dry.py").open("x",encoding="utf-8",newline="\n") as f:f.write(text)
print("V6_DRY_VARIANT_CREATED")
