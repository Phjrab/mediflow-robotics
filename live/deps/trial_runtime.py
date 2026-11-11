"""첫 시험의 장치 adapter. 정책 계산에는 bus를 전달하지 않는다."""
import hashlib
import json
from pathlib import Path
import time
import threading
from types import SimpleNamespace
from first_trial_dependencies import prepare
from first_trial_core import JOINTS,CAMERAS,Hold,sensor_gate,Limits,vector,home_gate,bounds_gate
from bounded_wait import wait

BASE=None
def dependencies():
    global BASE
    if BASE is None:BASE=prepare()
    return BASE

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def static_context():
    from config_runtime import load,path,contract
    import observation_contract as observations
    c=load();checkpoint=path(c['checkpoint']);calibration=path(c['calibration'])
    if sha(calibration)!=c['calibration_sha256'] or sha(checkpoint/'model.safetensors')!=c['model_sha256']:
        raise Hold('모델/보정 해시 불일치')
    mapping=observations.saved_mapping(checkpoint)
    expected={'observation.images.'+name:'observation.images.'+row['model_key'] for name,row in c['cameras'].items()}
    if mapping!=expected:raise Hold('저장 전처리의 카메라 대응 불일치')
    files={str(calibration):sha(calibration)}
    for name in ['config.json','policy_preprocessor.json','policy_postprocessor.json']:
        p=checkpoint/name;files[str(p)]=sha(p)
    return dict(checkpoint=checkpoint,calibration=calibration,home=c['home'],contract=contract(c),mapping=mapping,
                units=Path.home()/'units_so101.json',homefile=Path.home()/'home_state_so101.json',files=files,stop_inventory={})


def check_context(context):
    for name,digest in context['files'].items():
        if sha(Path(name))!=digest:raise Hold('실행 근거 파일 변경: '+Path(name).name)


class DeviceIO:
    physical=True
    def __init__(self,context,*,expected_torque=0,allow_goal=False,policy=None,continuation=None,current_limits=None):
        self.context=context;self.expected_torque=expected_torque;self.allow_goal=allow_goal;self.policy=policy
        self.robot=None;self.permission=None;self.cameras=[];self.authorized=False;self.cleanup_errors=[];self.closed=False
        self.read_history=[];self.last_fault=None;self.diagnostic_phase="INITIAL"
        self.reply_guard=None
        self.continuation=continuation
        self.current_limits=current_limits
        self.expected_wire=None
        self.before_wire=None
        from goal_telemetry import GoalTelemetry
        self.goal_telemetry=GoalTelemetry()
    def connect(self):
        if getattr(getattr(self.continuation,"start_review",None),"profile_only",False) and self.allow_goal:
            raise Hold("읽기 계측 경로의 목표 쓰기 금지")
        stop=Path(__file__).resolve().parent/"FIELD_STOP.json"
        if (stop.parent/"POST_HOLD_STOP.json").exists():
            from hold_continuation import HoldContinuation
            if not isinstance(self.continuation,HoldContinuation) or getattr(self.continuation,"start_review",None) is None:
                raise Hold("새 현장 중단 기록 존재; 추가 장치 접근 거부")
            self.continuation.require_claimed(self.context)
        if self.continuation is not None:
            from hold_continuation import HoldContinuation
            if not isinstance(self.continuation,HoldContinuation) or self.expected_torque!=1 or self.continuation.root!=stop.parent:
                raise Hold("토크 유지 연결 범위·위치·모드 불일치")
            self.continuation.require_claimed(self.context)
        elif stop.exists():raise Hold("현장 중단 기록 미검토; 추가 장치 접근 거부")
        dependencies();import probe_current_state as probe,device_preflight as devices
        devices.require();check_context(self.context)
        from lerobot.robots.so_follower import SO101Follower,SO101FollowerConfig
        from live_safety_runtime import PacketPermission
        io=self
        class Permission(PacketPermission):
            READS={**PacketPermission.READS,42:2,66:1,67:2,**({28:2,69:2} if io.current_limits is not None else {})}
            def inspect(self,port,packet):
                kind=super().inspect(port,packet)
                if kind=='goal':
                    io.goal_telemetry.capture(packet,io.robot.bus._normalize)
                    if io.current_limits is not None and io.goal_telemetry.active["wire_raw"] != io.expected_wire:
                        raise Hold("검증한 정수 목표와 SDK 패킷 불일치. 전송 중단")
                    if io.current_limits is not None:
                        io.before_wire()
                return kind
        self.robot=SO101Follower(SO101FollowerConfig(port=probe.PORT,id="so-101",calibration_dir=self.context["calibration"].parent,
                          cameras={},use_degrees=True,max_relative_target=2.,disable_torque_on_disconnect=False))
        table=self.robot.bus.model_ctrl_table
        for motor in self.robot.bus.motors.values():
            for name,pair in {"Goal_Position":(42,2),"Moving":(66,1),"Torque_Enable":(40,1)}.items():
                if tuple(table[motor.model][name])!=pair:raise Hold("설치 SDK 레지스터 계약 미확인")
            if self.current_limits is not None:
                for name,pair in {"Protection_Current":(28,2),"Present_Current":(69,2)}.items():
                    if tuple(table[motor.model][name])!=pair:raise Hold("설치 SDK 전류 레지스터 계약 미확인")
        self.permission=Permission(self.robot.bus.port_handler,allow_goal=self.allow_goal).install()
        from reply_integrity import StrictSyncReplies
        self.reply_guard=StrictSyncReplies(self.robot.bus.sync_reader,self.robot.bus.packet_handler).install()
        self.robot.bus.connect(handshake=False)
        import fcntl,termios
        fcntl.ioctl(self.robot.bus.port_handler.ser.fileno(),termios.TIOCEXCL)
        if self.current_limits is not None:
            if self.raw("Protection_Current") != self.current_limits:
                raise Hold("진단 이후 모터 전류 보호 설정이 달라짐")
        self.health_snapshot()
        from individual_reply_guard import StrictIndividualReplies
        with StrictIndividualReplies(self.robot.bus.packet_handler):
            if not self.robot.is_calibrated:raise Hold("모터/파일 보정 불일치")
    def raw(self,name):
        try:
            if self.continuation is not None:self.continuation.require_claimed(self.context)
            if self.reply_guard is not None:self.reply_guard.begin(name)
            values=self.robot.bus.sync_read(name,normalize=False,num_retry=0)
        except Exception as error:
            self.last_fault=dict(phase=self.diagnostic_phase,register=name,read_failed=True,error_kind=type(error).__name__,
                                 response=getattr(error,"observed",None),recent_reads=list(self.read_history))
            error.observed=self.last_fault
            raise
        import math
        def safe(value):
            if type(value) in (int,str,bool) or value is None:return value
            if type(value) is float and math.isfinite(value):return value
            return repr(value)[:120]
        captured={str(k):safe(v) for k,v in values.items()} if isinstance(values,dict) else {"received_type":type(values).__name__,"repr":repr(values)[:240]}
        self.read_history.append(dict(unix=time.time(),phase=self.diagnostic_phase,register=name,values=captured))
        self.read_history=self.read_history[-32:]
        if not isinstance(values,dict) or set(values)!=set(JOINTS) or any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values()):
            raise self.observed_hold("RAW 센서 오류",register=name,values=captured)
        return values
    def observed_hold(self,reason,**observed):
        self.last_fault=dict(phase=self.diagnostic_phase,expected_torque=self.expected_torque,**observed,recent_reads=list(self.read_history))
        error=Hold(reason);error.observed=self.last_fault
        return error
    def health_snapshot(self):
        torque=self.raw("Torque_Enable")
        if any(type(v) is not int or v!=self.expected_torque for v in torque.values()):raise self.observed_hold("예상 토크 상태 불일치; 추가 읽기/쓰기 중단",torque=dict(torque))
        temps=self.raw("Present_Temperature")
        threshold=45 if self.expected_torque==0 else 55
        if any(not 0<=t<threshold for t in temps.values()):raise self.observed_hold("온도 상한; 추가 읽기/쓰기 중단",temperatures=dict(temps),threshold=threshold)
        modes=self.raw("Operating_Mode");loads=self.raw("Present_Load");status=self.raw("Status")
        row=dict(torque=torque,modes=modes,temperatures=temps,loads=loads,statuses=status)
        try:sensor_gate(row,Limits(),expected_torque=self.expected_torque,require_state=False)
        except Hold as error:
            self.last_fault=dict(phase=self.diagnostic_phase,snapshot=row,recent_reads=list(self.read_history))
            error.observed=self.last_fault
            raise
        if self.current_limits is not None:
            currents=self.raw("Present_Current")
            if any(type(currents[j]) is not int or abs(currents[j])>=self.current_limits[j] for j in JOINTS):
                raise self.observed_hold("전류 보호 한계. 추가 목표 전송 중단",currents=currents,limits=self.current_limits)
            row["currents"]=currents
        return row
    def snapshot(self):
        row=self.health_snapshot()
        if self.current_limits is None:
            pos=self.robot.bus.sync_read("Present_Position",num_retry=0)
        else:
            bus=self.robot.bus
            ids={name:bus.motors[name].id for name in JOINTS}
            normalized=bus._normalize({ids[name]:value for name,value in self.raw("Present_Position").items()})
            pos={name:normalized[ids[name]] for name in JOINTS}
        row["state"]=vector([float(pos[j]) for j in JOINTS])
        moving=self.raw("Moving")
        if any(type(v) is not int or v not in (0,1) for v in moving.values()):raise Hold("Moving 응답 미확인")
        row["moving"]=any(moving.values());return row
    def joint_bounds(self):
        result=[];bus=self.robot.bus
        for name in JOINTS:
            cal=bus.calibration[name];motor_id=bus.motors[name].id
            if type(cal.range_min) is not int or type(cal.range_max) is not int or not 0<=cal.range_min<cal.range_max<=4095:raise Hold("보정 범위 미확인")
            pair=[float(bus._normalize({motor_id:raw})[motor_id]) for raw in (cal.range_min,cal.range_max)]
            result.append(sorted(pair))
        return result
    def inspect_goal(self):
        snap=self.snapshot();home_gate(snap["state"],self.context["home"])
        present=self.raw("Present_Position");goal=self.raw("Goal_Position")
        from individual_reply_guard import StrictIndividualReplies,read_target_feedback
        with StrictIndividualReplies(self.robot.bus.packet_handler):
            active_target=read_target_feedback(self.robot.bus,JOINTS)
        for values in (present,goal,active_target):
            if any(type(v) is not int or not 0<=v<=4095 for v in values.values()):raise Hold("원시 목표/현재 위치 범위 오류")
        bus=self.robot.bus
        goal_state=[float(bus._normalize({bus.motors[j].id:goal[j]})[bus.motors[j].id]) for j in JOINTS]
        active_state=[float(bus._normalize({bus.motors[j].id:active_target[j]})[bus.motors[j].id]) for j in JOINTS]
        bounds=self.joint_bounds();within=True
        try:bounds_gate(snap["state"],bounds);bounds_gate(active_state,bounds)
        except Hold:within=False
        from start_goal_agreement import assess
        current_state=[float(bus._normalize({bus.motors[j].id:present[j]})[bus.motors[j].id]) for j in JOINTS]
        agreement=assess(present,active_target,current_state,active_state,bounds)
        continuity_result=None
        review=getattr(self.continuation,"start_review",None)
        if getattr(review,"repeat_live",False):
            from cadence_repeat import continuity
            quantum=[]
            for j in JOINTS:
                mid=bus.motors[j].id;raw=active_target[j];other=raw-1 if raw else raw+1
                quantum.append(abs(float(bus._normalize({mid:raw})[mid])-float(bus._normalize({mid:other})[mid])))
            continuity_result=continuity(current_state,active_state,review.expected_last_goal,quantum,bounds,encoding_allowance=getattr(review,"quantized_repeat",False),tracking_cap=getattr(review,"tracking_cap_degrees",.5))
        return dict(status="ARM_INSPECTION_READ_ONLY",snapshot=snap,present_raw=present,goal_raw=goal,goal_state=goal_state,
                    raw_delta={j:goal[j]-present[j] for j in JOINTS},goal_exact_match=goal==present,
                    target_feedback_raw=active_target,target_feedback_state=active_state,active_target_exact_match=active_target==present,
                    joint_bounds=bounds,bounds_contain_current_and_active_target=within,
                    start_agreement=agreement,repeat_continuity=continuity_result,
                    arm_ready=(continuity_result["passed"] if continuity_result is not None else agreement["start_check_passed"]) and within,
                    register_writes=self.permission.goal_packets)
    def connect_cameras(self):
        from lerobot.cameras.opencv import OpenCVCameraConfig
        from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
        self.camera_map={}
        for name in CAMERAS:
            c=OpenCVCamera(OpenCVCameraConfig(index_or_path=self.context["contract"]["cameras"][name],fps=30,width=640,height=480,color_mode="rgb",warmup_s=3,backend=200))
            self.cameras.append(c);self.camera_map[name]=c
            wait(c.connect,self.health_snapshot,deadline=time.monotonic()+12,timeout=12)
    def motor_quantum(self):
        bus=self.robot.bus;result=[]
        for name in JOINTS:
            mid=bus.motors[name].id;raw=bus.calibration[name].range_min
            result.append(abs(float(bus._normalize({mid:raw+1})[mid])-float(bus._normalize({mid:raw})[mid])))
        return result
    def capture_frames(self):
        import numpy as np
        result={}
        for name in CAMERAS:
            frame=np.asarray(self.camera_map[name].async_read(timeout_ms=500)).copy()
            result[name]=dict(pixels=frame,shape=list(frame.shape),dtype=str(frame.dtype),host_received=time.monotonic(),sha256=hashlib.sha256(frame.tobytes()).hexdigest())
        return result
    def predict(self,frames,state):return self.policy.predict(frames,state)
    def check_records(self):
        check_context(self.context)
        if self.continuation is not None:self.continuation.require_claimed(self.context)
    def send_target(self,target):
        if not self.authorized or not self.allow_goal or self.expected_torque!=1:raise Hold("목표 전송 권한 없음")
        self.goal_telemetry.begin(target)
        try:
            with self.permission.goal_once():returned=self.robot.send_action({j+".pos":target[i] for i,j in enumerate(JOINTS)})
            return self.goal_telemetry.finish(returned)
        except BaseException as error:
            self.goal_telemetry.fail(error)
            raise
    def encoder_axes(self):
        from data_motion import Axis
        bus=self.robot.bus
        axes=[]
        for name in JOINTS:
            motor=bus.motors[name].id
            cal=bus.calibration[name]
            low=float(bus._normalize({motor:cal.range_min})[motor])
            high=float(bus._normalize({motor:cal.range_max})[motor])
            axes.append(Axis(cal.range_min,cal.range_max,low,high))
        return axes
    def send_encoded_target(self,normalized,raw,check):
        if self.current_limits is None or not self.authorized or not self.allow_goal or self.expected_torque!=1:
            raise Hold("정수 목표 전송 권한 없음")
        self.goal_telemetry.begin(normalized)
        self.expected_wire=dict(raw)
        self.before_wire=check
        try:
            with self.permission.goal_once():
                self.robot.bus.sync_write("Goal_Position",raw,normalize=False,num_retry=0)
            return self.goal_telemetry.finish_encoded()
        except BaseException as error:
            self.goal_telemetry.fail(error)
            raise
        finally:
            self.expected_wire=None
            self.before_wire=None
    def close(self):
        if self.closed:return
        self.closed=True
        if self.robot is not None:
            from live_safety_runtime import close_without_writes,finish_workers_after_port_close
            self.cleanup_errors=close_without_writes(self.robot,self.cameras,bool(self.robot.bus.is_connected),self.permission)
            if self.reply_guard is not None and not self.robot.bus.is_connected:self.reply_guard.restore()
            finish_workers_after_port_close(10)
        if self.policy is not None:self.policy.close()

class InspectionAdapter:
    def __init__(self,io):self.io=io
    def connect(self):self.io.connect()
    def read_status(self):return self.io.inspect_goal()
    def close(self):self.io.close()

class PolicyProvider:
    """저장 전/후처리와 CUDA 계산은 장치 권한 없는 별도 프로세스에 둔다."""
    def __init__(self,context,task):
        self.closed=False
        dependencies();import torch
        from process_policy import ProcessPolicy
        from persistent_policy_worker import PersistentPolicyWorker
        self.torch=torch;self.context=context;self.task=task
        self.policy=None;self.worker=None
        try:
            self.policy=ProcessPolicy.from_pretrained(context["checkpoint"],local_files_only=True)
            self.worker=PersistentPolicyWorker()
            import numpy as np
            frames={name:dict(pixels=np.zeros((480,640,3),dtype=np.uint8)) for name in CAMERAS}
            for _ in range(2):self.predict(frames,context["home"]["state"])
            self.worker.call(self.policy.reset,label="reset_after_warmup")
        except BaseException as error:
            try:self.close()
            except BaseException as cleanup_error:error.initialization_cleanup_error=type(cleanup_error).__name__
            raise
    def predict(self,frames,state):
        def compute():
            import observation_contract as observation
            packed=observation.pack_observation({k:v["pixels"] for k,v in frames.items()},state,self.task,self.torch,self.context["contract"])
            return self.policy.predict_raw(packed)
        return self.worker.call(compute,label="isolated_first_trial_policy")
    def close(self):
        if self.closed:return
        self.closed=True
        try:
            if self.worker is not None:return self.worker.close()
        finally:
            if self.policy is not None:self.policy.close()
