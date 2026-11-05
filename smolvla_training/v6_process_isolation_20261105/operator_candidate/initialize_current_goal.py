"""현장 운영자 전용 1회 목표 초기화 시험. 기본은 계획 출력이며 이 개발 작업에서는 실행하지 않는다."""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

JOINTS=("shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper")

def exact_packet(values):
    if set(values)!=set(JOINTS) or any(type(v) is not int or not 0<=v<=4095 for v in values.values()):raise ValueError("원시 현재 위치 형식 오류")
    params=bytes((42,2))+b"".join(bytes((i,))+values[j].to_bytes(2,"little") for i,j in enumerate(JOINTS,1))
    header=bytes((255,255,254,len(params)+2,131))+params
    return header+bytes(((~sum(header[2:]))&255,))

def goal_permission(base):
    class ExactCurrentGoalPermission(base):
        READS={**base.READS,42:2,66:1}
        def authorize(self,values):
            if getattr(self,"expected_goal",None) is not None or self.goal_packets:raise RuntimeError("목표 초기화 재시도 금지")
            self.expected_goal=exact_packet(values)
            self.goal_deadline=time.monotonic()+.1
        def inspect(self,port,packet):
            kind=super().inspect(port,packet)
            if kind=="goal":
                if getattr(self,"expected_goal",None)!=bytes(packet) or time.monotonic()>self.goal_deadline:
                    self.blocked_packets+=1;raise RuntimeError("확인한 현재 위치와 다른 목표 패킷 차단")
                self.expected_goal=None
            return kind
    return ExactCurrentGoalPermission

class InitializationAdapter:
    physical=True
    def __init__(self,io,context):self.io=io;self.context=context;self.result=None;self.stopped=False;self.previous_read_packets=0;self.attempted_target=None;self.stop_observed=None
    def connect(self):self.io.connect()
    def read_state(self):
        snapshot=self.io.snapshot()
        return SimpleNamespace(moving=snapshot["moving"])
    def prepare(self):
        from first_trial_core import home_gate,bounds_gate
        motors=self.io.robot.bus.motors
        if tuple(motors)!=JOINTS or [m.id for m in motors.values()]!=list(range(1,7)):raise RuntimeError("관절/모터ID 대응 불일치")
        row=self.io.snapshot();home_gate(row["state"],self.context["home"]);bounds_gate(row["state"],self.io.joint_bounds())
        self.io.check_records()
    def execute_stage(self,stage):
        if stage!="initialize_current_goal" or self.stopped:raise RuntimeError("초기화 단계/정지 조건 오류")
        from first_trial_core import home_gate,bounds_gate
        from live_safety_runtime import PacketPermission
        row=self.io.snapshot();home_gate(row["state"],self.context["home"]);bounds_gate(row["state"],self.io.joint_bounds())
        current=self.io.raw("Present_Position")
        if self.io.raw("Present_Position")!=current:raise RuntimeError("준비 중 원시 현재 위치 변화; 쓰기 없이 중단")
        for joint in JOINTS:
            cal=self.io.robot.bus.calibration[joint]
            if not cal.range_min<=current[joint]<=cal.range_max:raise RuntimeError("보정 범위 이탈")
        self.io.health_snapshot();self.io.check_records()
        # 단일 소유 스레드에서 보호를 교체한다. 이 구간에는 SDK 송신 호출이 없다.
        old=self.io.permission
        if old.goal_packets or old.blocked_packets:raise RuntimeError("기존 보호 상태 이상")
        self.previous_read_packets=old.read_packets
        old.seal();old.restore()
        permission=goal_permission(PacketPermission)(self.io.robot.bus.port_handler,allow_goal=True).install()
        self.io.permission=permission
        # 보호 교체 후 다시 읽은 현재 위치만 정확한 1회 패킷으로 허용한다.
        self.io.health_snapshot()
        fresh=self.io.raw("Present_Position")
        if fresh!=current:raise RuntimeError("쓰기 직전 원시 위치 변화; 쓰기 없이 중단")
        permission.authorize(current)
        self.attempted_target=dict(current)
        with permission.goal_once():self.io.robot.bus.sync_write("Goal_Position",current,normalize=False,num_retry=0)
        # 자동 토크ON/OFF나 재시도 없음. 예상 밖 토크ON이면 추가 읽기도 중단한다.
        torque=self.io.raw("Torque_Enable")
        if any(type(v) is not int or v!=0 for v in torque.values()):
            self.stop_observed=dict(torque=torque)
            raise RuntimeError("초기화 후 토크ON/미확인; 현장 지지 유지, 전원 차단 판단 필요")
        self.io.health_snapshot()
        actual_goal=self.io.raw("Goal_Position");after=self.io.raw("Present_Position")
        if actual_goal!=current or after!=current:raise RuntimeError("초기화 후 목표/현재 위치 불일치; 추가 목표 전송 금지")
        self.io.check_records()
        self.result=dict(status="GOAL_INITIALIZED_TORQUE_OFF",goal_raw=actual_goal,present_raw=after,torque=torque,goal_packets=permission.goal_packets,
                         torque_packets=0,live_authorized=False)
    def stop_new_commands(self):self.stopped=True
    def close(self):self.io.close()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--operator-present",action="store_true")
    parser.add_argument("--supported",action="store_true")
    parser.add_argument("--stop-ready",action="store_true")
    parser.add_argument("--out",type=Path)
    args=parser.parse_args(argv)
    if not args.execute:
        print("PLAN_ONLY: 장치 접근·모터 쓰기0. 현장 검증 전 초기화 기능은 NOT_RUN.");return 0
    if not all((args.operator_present,args.supported,args.stop_ready)) or args.out is None:raise RuntimeError("현장 입회·팔 지지·전원 차단 수단·새 출력 경로 필요")
    root=Path(__file__).parent
    if (root/"FIELD_STOP.json").exists():raise RuntimeError("현장 중단 기록 보존 중; 검토 전 재시도 금지")
    from operator_arm import confirm
    confirm("INITIALIZE_GOAL","현장 시험: 기존 토크ON 중단 기록을 검토하세요. 목표 쓰기가 토크를 켤 가능성은 미확인입니다. 팔을 받치고 즉시 전원을 차단할 수 있을 때만 진행하세요.")
    out=args.out.expanduser();out.mkdir(parents=True,exist_ok=False)
    from trial_runtime import static_context,DeviceIO
    from first_trial_core import FirstMotionPlan
    from controller_bridge import load_controller
    context=static_context();io=DeviceIO(context,expected_torque=0,allow_goal=False);adapter=InitializationAdapter(io,context)
    result=dict(status="HOLD",live="NOT_RUN",operator_only=True)
    try:
        load_controller()(adapter).run(FirstMotionPlan(True,stages=("initialize_current_goal",)))
        result.update(adapter.result)
    except BaseException as e:
        result["error"]=dict(kind=type(e).__name__,reason=str(e).replace(str(Path.home()),"~"))
    finally:
        result["goal_packets"]=io.permission.goal_packets if io.permission else 0
        result["cleanup_errors"]=io.cleanup_errors
        result["attempted_target_raw"]=adapter.attempted_target
        result["stop_observed"]=adapter.stop_observed
        try:
            import device_preflight
            result["devices_after"]=device_preflight.inspect()
            if result["devices_after"]["all_safe"] is not True:result["status"]="HOLD"
        except Exception as error:
            result["status"]="HOLD";result["cleanup_check_error"]=type(error).__name__
        if result["cleanup_errors"]:result["status"]="HOLD"
        if result["status"]=="HOLD":
            with (root/"FIELD_STOP.json").open("x",encoding="utf-8") as f:json.dump(dict(unix=time.time(),**result),f,ensure_ascii=False,indent=2)
        with (out/"RESULT.json").open("x",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False))
    return 0 if result["status"]=="GOAL_INITIALIZED_TORQUE_OFF" and not result["cleanup_errors"] else 2

if __name__=="__main__":raise SystemExit(main())
