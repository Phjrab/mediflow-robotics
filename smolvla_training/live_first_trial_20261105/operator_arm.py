"""현장 운영자 전용 재무장 adapter. 이 개발 세션에서는 실행하지 않는다."""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from first_trial_core import JOINTS,Hold,home_gate,vector

ARM_SHA="6d398d3306498699cb45975033f9e03c173edaf088bc9d63d28bbdf64ac2d3fe"

def confirm(token,prompt,*,monitor=None):
    if not sys.stdin.isatty():raise Hold("현장 터미널의 직접 입력 필요; 자동 입력 거부")
    if monitor is None:
        answer=input(prompt+"\n"+token+" 입력: ").strip()
    else:
        import select
        print(prompt+"\n"+token+" 입력(30초 이내): ",end="",flush=True)
        deadline=time.monotonic()+30;last=time.monotonic()
        while True:
            now=time.monotonic()
            if now>=deadline or now-last>.25:raise Hold("현장 입력 대기 시간/감시 주기 초과")
            last=now;monitor()
            ready,_,_=select.select([sys.stdin],[],[],.05)
            if ready:
                answer=sys.stdin.readline().strip();monitor();break
    if answer!=token:raise Hold("현장 확인 불일치")

class ArmingAdapter:
    physical=True
    def __init__(self,context,emit,*,release=False):
        self.context=context;self.emit=emit;self.release=release;self.stack=contextlib.ExitStack()
        self.robot=None;self.guard=None;self.api=None;self.result=None;self.stopped=False
    def connect(self):
        from trial_runtime import dependencies,check_context
        dependencies()
        import arm_source as arm,device_preflight as devices,probe_current_state as probe
        path=Path(__file__).parent/"arm_source.py"
        if hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()!=ARM_SHA:raise Hold("검토된 재무장 소스와 다름")
        devices.require();check_context(self.context)
        source=Path.home()/"so101_first_action_test_safe_20261104_v4.py"
        if hashlib.sha256(source.read_bytes()).hexdigest()!=arm.V4_HASH:raise Hold("기존 정책 API 소스와 다름")
        self.api=arm.load_v4(source)
        from lerobot.robots.so_follower import SO101Follower,SO101FollowerConfig
        import scservo_sdk
        self.guard=arm.WriteGuard("disable" if self.release else "enable",self.emit)
        self.stack.enter_context(self.guard.installed(scservo_sdk.PortHandler))
        self.robot=SO101Follower(SO101FollowerConfig(port=probe.PORT,id="so-101",calibration_dir=self.context["calibration"].parent,
                    cameras={},use_degrees=True,max_relative_target=2.,disable_torque_on_disconnect=False))
        arm.validate_sdk(self.robot.bus);self.guard.phase="open";self.robot.bus.connect(handshake=False)
        import fcntl,termios
        fcntl.ioctl(self.robot.bus.port_handler.ser.fileno(),termios.TIOCEXCL)
        torque=arm.fresh_torque(self.robot.bus,self.api)
        if not self.release and any(torque.values()):raise Hold("재무장 시작 토크ON; 추가 읽기/쓰기 중단")
        temperatures=self.api.read_temperatures(self.robot.bus)
        if any(not 0<=v<50 for v in temperatures.values()):raise Hold("시작 온도50도 미만 필요")
        if not self.robot.is_calibrated:raise Hold("모터와 보정 파일 불일치")
        self.guard.phase="state_check"
    def read_state(self):
        import arm_source as arm
        torque,modes,positions=arm.diagnostics(self.robot.bus,self.api,self.context["home"])
        self.api.read_health(self.robot.bus,starting=True)
        if any(m!=0 for m in modes.values()):raise Hold("위치모드0 필요")
        if not self.release:
            if any(torque.values()):raise Hold("토크OFF 필요")
            home_gate(vector([float(positions[j]) for j in JOINTS]),self.context["home"])
            present=arm.read_joints(self.robot.bus,"Present_Position",normalize=False)
            goal=arm.read_joints(self.robot.bus,"Goal_Position",normalize=False)
            if goal!=present:raise Hold("기존 목표와 현재 원시 위치 불일치; 자동 목표 수정 금지")
        moving=arm.read_joints(self.robot.bus,"Moving",normalize=False)
        if any(type(v) is not int or v not in (0,1) for v in moving.values()):raise Hold("Moving 응답 미확인")
        return SimpleNamespace(moving=any(moving.values()))
    def prepare(self):
        from trial_runtime import check_context
        check_context(self.context)
        confirm("SUPPORTED" if self.release else "ENABLE",
                "현장에서 팔을 지지하고 회전부에서 손을 빼세요. 전원 차단 수단을 확인하세요.")
    def execute_stage(self,stage):
        if stage!="manual_torque" or self.stopped:raise Hold("재무장 단계 오류/정지")
        import arm_source as arm
        from trial_runtime import check_context
        check_context(self.context)
        self.result=(arm.release(self.robot.bus,self.api,self.guard,self.context["home"]) if self.release else
                     arm.arm(self.robot.bus,self.api,self.context["home"],self.guard))
        self.emit(dict(event="OPERATOR_TORQUE_COMPLETE",mode="disable" if self.release else "enable",result=self.result))
    def stop_new_commands(self):self.stopped=True
    def close(self):
        try:
            if self.robot is not None and self.robot.bus.is_connected:
                import arm_source as arm
                arm.close_bus(self.robot.bus)
        finally:self.stack.close()
