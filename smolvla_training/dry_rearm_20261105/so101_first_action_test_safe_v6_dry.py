#!/usr/bin/env python3
"""SmolVLA 첫 동작 시험. 기본 dry-run은 모터 명령을 보내지 않는다."""

import argparse
import csv
import json
import math
import hashlib
import struct
import os
from pathlib import Path
import sys
import time
import traceback
from live_safety_runtime import (Hold, PacketPermission, check_stop_record,
                                 monitored_inference, close_without_writes,
                                 finish_workers_after_port_close)
from dry_session import DrySession, home_path
from checkpoint_amp import saved_amp, infer_with_saved_amp
from persistent_policy_worker import PersistentPolicyWorker


JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
CAMERAS = ("end_effector", "ceiling_vertical", "ceiling_oblique")
COLOR_TASKS = {
    "red": "Put medicine bottle A into the red basket",
    "green": "Put medicine bottle A into the green basket",
    "blue": "Put medicine bottle A into the blue basket",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-session",type=Path,required=True,help="1회 읽기 전용 재개 확인서")
    parser.add_argument("--port", default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00", help="이전 물리 확인 follower by-id; 현재 연결은 사용자 확인")
    parser.add_argument("--robot-id", required=True, help="LeLab SO101 보정 파일에 쓰는 id")
    parser.add_argument("--calibration-dir", help="기본 LeRobot 보정 폴더 대신 쓸 폴더")
    parser.add_argument("--end-effector-camera", default=None, help="손목 V4L2 경로 또는 번호")
    parser.add_argument("--ceiling-vertical-camera", default=None, help="Astra V4L2 경로 또는 번호")
    parser.add_argument("--ceiling-oblique-camera", default=None, help="RealSense V4L2 경로 또는 번호")
    parser.add_argument("--task", required=True, choices=tuple(COLOR_TASKS.values()))
    parser.add_argument("--checkpoint", default="~/smolvla_model/pretrained_model")
    parser.add_argument("--out", default="~/smolvla_first_action")
    parser.add_argument("--max-seconds", type=float, default=5.0)
    parser.add_argument("--step-cap", type=float, default=0.5, help="5축 스텝당 도 제한, 최대 2")
    parser.add_argument("--max-deg-per-second", type=float, default=20.0)
    parser.add_argument("--first-action-limit", type=float, default=10.0)
    parser.add_argument("--load-limit", type=float, default=200.0, help="Present_Load 절댓값 원시 단위; 퍼센트 아님")
    parser.add_argument("--home-state", type=Path, help="검증한 home JSON: joint_names, state[6], tolerance_deg")
    parser.add_argument("--unit-evidence", type=Path, help="학습/현재 보정 대응을 확인한 근거 JSON")
    parser.add_argument("--temperature-every", type=int, default=5, help="실행 중 온도를 읽는 간격(프레임)")
    parser.add_argument("--fps", type=float, default=30.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값. 추론·기록만 하고 send_action은 호출하지 않음")
    mode.add_argument("--live", action="store_true", help="실제 관절 목표를 보냄")
    return parser.parse_args()


def vector(values):
    if len(values) != 6 or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in values):
        raise ValueError("HOLD: 6차원 유한 수치 필요")
    if not 0 <= values[5] <= 100:
        raise ValueError("HOLD: 그리퍼 0~100 범위 이탈")
    return list(values)


def rate_config(fps, cap, speed):
    if not all(math.isfinite(x) and x > 0 for x in (fps, cap, speed)) or cap > 2:
        raise ValueError("주기/각도/속도 설정 오류")
    if cap * fps > speed + 1e-9:
        raise ValueError("시작 거부: 스텝 제한 × Hz가 초당 각도 상한을 넘음")


def first_action_gate(action, state, limit):
    vector(action); vector(state)
    if not math.isfinite(limit) or limit <= 0 or any(abs(action[i] - state[i]) >= limit for i in range(5)):
        raise ValueError("HOLD: 첫 action 큰 점프")


def home_gate(state, home):
    if not home or home.get("joint_names") != list(JOINTS):
        raise ValueError("HOLD: home 근거 미확인")
    target = vector(home["state"])
    tolerance = float(home["tolerance_deg"])
    if not math.isfinite(tolerance) or tolerance <= 0 or any(abs(state[i] - target[i]) > tolerance for i in range(5)):
        raise ValueError("HOLD: 시작 자세가 home 범위 밖")


def health_gate(temperatures, loads, status, starting=False, load_limit=200.0):
    for values in (temperatures, loads, status):
        if set(values) != set(JOINTS) or any(not math.isfinite(float(v)) for v in values.values()):
            raise ValueError("HOLD: 모터 상태 읽기 오류")
    threshold = 50.0 if starting else 55.0
    if any(t < 0 or t >= threshold for t in temperatures.values()):
        raise ValueError("HOLD: 온도 상한")
    if any(abs(v) >= load_limit for v in loads.values()) or any(v != 0 for v in status.values()):
        raise ValueError("HOLD: 부하 임계값 또는 모터 Status 오류")


def safe_target(action, measured, previous, cap, fps, speed):
    rate_config(fps, cap, speed)
    vector(action); vector(measured); vector(previous)
    result = []
    for i in range(6):
        step = cap if i < 5 else 2.0
        velocity_step = speed / fps if i < 5 else 2.0
        low = max(measured[i] - step, previous[i] - velocity_step)
        high = min(measured[i] + step, previous[i] + velocity_step)
        if low > high:
            raise ValueError("HOLD: 측정 자세와 이전 목표의 제한 범위가 겹치지 않음")
        result.append(min(high, max(low, action[i])))
    return vector(result)


class FrameWatch:
    def __init__(self):
        self.previous = {}

    def check(self, frames):
        if set(frames) != set(CAMERAS):
            raise ValueError("HOLD: 카메라 키 누락")
        for key, payload in frames.items():
            digest = hashlib.sha256(payload).hexdigest()
            if self.previous.get(key) == digest:
                raise ValueError("HOLD: 카메라 반복 프레임 후보: " + key)
            self.previous[key] = digest


def read_health(bus,starting=False,load_limit=200.0):
    temps={}
    for joint in JOINTS:
        t=bus.read("Present_Temperature",joint,normalize=False,num_retry=0)
        temps[joint]=t
        if type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<45:
            raise Hold("dry-run 45도 이상/미확인; 추가 serial 읽기 중단")
    loads={j:float(bus.read("Present_Load",j,normalize=False,num_retry=0)) for j in JOINTS}
    status={j:int(bus.read("Status",j,normalize=False,num_retry=0)) for j in JOINTS}
    health_gate(temps,loads,status,starting,load_limit)
    return temps


def inspect_checkpoint(checkpoint):
    config = json.loads((checkpoint / "config.json").read_text())
    pre = json.loads((checkpoint / "policy_preprocessor.json").read_text())
    post = json.loads((checkpoint / "policy_postprocessor.json").read_text())
    mappings = [s["config"]["rename_map"] for s in pre["steps"] if s.get("registry_name") == "rename_observations_processor"]
    if len(mappings) != 1:
        raise ValueError("저장 카메라 rename 설정을 하나로 확인할 수 없음")
    sources = {"observation.images." + k for k in CAMERAS}
    mapping = mappings[0]
    expected = {k for k in config["input_features"] if k.startswith("observation.images.")}
    if config["input_features"].get("observation.state", {}).get("shape") != [6]:
        raise ValueError("체크포인트 state 차원이 6이 아님")
    if set(mapping) != sources or set(mapping.values()) != expected or len(set(mapping.values())) != 3:
        raise ValueError("체크포인트 입력 feature와 저장 rename 매핑 불일치")
    stats = []
    for pipe in (pre, post):
        for step in pipe["steps"]:
            if "state_file" in step:
                name = step["state_file"]
                if Path(name).name != name or not (checkpoint / name).is_file():
                    raise ValueError("정규화 통계 파일 누락/경로 오류")
                stats.append(name)
    if len(stats) < 2:
        raise ValueError("입출력 정규화 통계가 불완전함")
    train_path = checkpoint / "train_config.json"
    train = json.loads(train_path.read_text()) if train_path.is_file() else {}
    candidate = config.get("type") == "smolvla" and config.get("output_features", {}).get("action", {}).get("shape") == [6] and int(train.get("steps", 0)) > 0
    summary = {}
    for name in stats:
        with (checkpoint / name).open("rb") as stream:
            size = struct.unpack("<Q", stream.read(8))[0]
            if size > 1024 * 1024:
                raise ValueError("통계 헤더 크기 오류")
            header = json.loads(stream.read(size))
            for key, item in header.items():
                if key == "__metadata__":
                    continue
                if item.get("shape") == [6] and item.get("dtype") == "F32" and key.endswith((".min", ".max", ".mean")):
                    begin, end = item["data_offsets"]
                    if end - begin != 24:
                        raise ValueError("통계 길이 오류")
                    stream.seek(8 + size + begin)
                    values = list(struct.unpack("<6f", stream.read(24)))
                    if not all(math.isfinite(v) for v in values):
                        raise ValueError("정규화 통계 NaN/Inf")
                    summary[name + ":" + key] = values
    if not summary:
        raise ValueError("6축 정규화 통계를 확인할 수 없음")
    return {"checkpoint_kind": "SO101_FINETUNED_CANDIDATE" if candidate else "BASE_OR_UNVERIFIED",
            "camera_mapping": mapping, "normalization_files": stats, "normalization_summary": summary,
            "units_and_calibration": "UNVERIFIED"}


def calculate_only(robot, observe, predict):
    state, observation, frames = observe(robot)
    vector(state)
    actions = predict(observation)
    if len(actions) != 50:
        raise ValueError("HOLD: action chunk 길이 오류")
    for action in actions:
        vector(action)
    return state, actions, frames


def verify_units(evidence, checkpoint, calibration_file):
    if not evidence:
        raise ValueError("live 거부: 단위/관절순서/보정 대응 UNVERIFIED")
    data = json.loads(evidence.expanduser().read_text())
    if data.get("joint_names") != list(JOINTS) or data.get("units") != ["degree"] * 5 + ["percent_0_100"] or not data.get("evidence"):
        raise ValueError("live 거부: 단위 근거 불완전")
    digest = hashlib.sha256(calibration_file.read_bytes()).hexdigest()
    if data.get("current_calibration_sha256") != digest or data.get("training_calibration_sha256") != digest:
        raise ValueError("live 거부: 학습/현재 보정 대응 불일치")
    h = hashlib.sha256()
    with (checkpoint / "model.safetensors").open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    if data.get("model_sha256") != h.hexdigest():
        raise ValueError("live 거부: 모델 근거 해시 불일치")


def as_device(value):
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return Path(value).expanduser() if isinstance(value, str) else value


def make_v4l2_configs(factory, devices):
    configs = {}
    for name, value in devices.items():
        configs[name] = factory(index_or_path=as_device(value), fps=30, width=640, height=480,
                                color_mode="rgb", warmup_s=3, backend=200)
    return configs


def build_observation(robot, torch, np):
    raw = robot.get_observation()
    state = [float(raw[joint + ".pos"]) for joint in JOINTS]
    if len(state) != 6 or not all(map(lambda x: __import__("math").isfinite(x), state)):
        raise ValueError("관절 상태가 6개 유한값이 아님")
    packed = {"observation.state": torch.tensor([state], dtype=torch.float32)}
    frames = {}
    for name in CAMERAS:
        frame = np.asarray(raw[name])
        if frame.shape != (480, 640, 3) or frame.dtype != np.uint8:
            raise ValueError("카메라 형식 불일치: %s shape=%r dtype=%s" % (name, frame.shape, frame.dtype))
        # 카메라 출력은 OpenCV config에서 RGB를 요청한다. LeRobot 데이터와 같이 RGB CHW [0,1]로 만든다.
        frames[name] = frame.copy()
        key = "observation.images." + name
        packed[key] = torch.from_numpy(frame.copy()).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    return state, packed, frames


def read_temperatures(bus):
    return {joint: float(bus.read("Present_Temperature", joint, normalize=False, num_retry=0)) for joint in JOINTS}


def read_motor_state(bus):
    torque,modes={},{}
    for joint in JOINTS:
        value=bus.read("Torque_Enable",joint,normalize=False,num_retry=0)
        torque[joint]=value
        if type(value) is not int or value!=0:raise Hold("dry-run 중 토크ON/미확인 감지; 추가 serial 읽기 중단")
    for joint in JOINTS:
        value=bus.read("Operating_Mode",joint,normalize=False,num_retry=0)
        modes[joint]=value
        if type(value) is not int or value!=0:raise Hold("dry-run 위치 모드0 아님")
    return torque,modes


def motor_mode_gate(torque, modes, live):
    if set(torque) != set(JOINTS) or set(modes) != set(JOINTS):
        raise RuntimeError("모터 상태 키 누락")
    if live and any(value != 1 for value in torque.values()):
        raise RuntimeError("live 거부: 모든 모터 토크가 이미 켜져 있지 않음; 자동 활성화하지 않음: %r" % torque)
    if live and any(value != 0 for value in modes.values()):
        raise RuntimeError("live 거부: 모든 모터가 위치 모드(0)가 아님; 설정을 쓰지 않음: %r" % modes)


def infer_chunk(policy, preprocess, postprocess, observation, torch):
    batch = preprocess(dict(observation))
    with torch.inference_mode():
        normalized = policy.predict_action_chunk(batch)
        if tuple(normalized.shape) != (1, 50, 6):
            raise ValueError("정책 action chunk shape 오류: %r" % (tuple(normalized.shape),))
        if not bool(torch.isfinite(normalized).all()):
            raise ValueError("정책 action chunk에 NaN/Inf 포함")
        actions = []
        for step in range(50):
            action = postprocess(normalized[:, step, :])
            if hasattr(action, "detach"):
                action = action.detach().float().cpu().reshape(-1).tolist()
            elif isinstance(action, dict):
                action = [float(action[joint + ".pos"]) for joint in JOINTS]
            else:
                action = list(action)
            if len(action) != 6 or not all(__import__("math").isfinite(float(x)) for x in action):
                raise ValueError("후처리 action 차원 또는 수치 오류")
            actions.append([float(x) for x in action])
    return actions




def close_session(robot, cameras, live, attempted, connected):
    errors = close_without_writes(robot, cameras, connected, getattr(robot, "_v6_permission", None))
    print("종료: 추가 Goal/토크 쓰기 없음. 기존 목표/토크 유지이며 즉시 물리 정지 보장은 아님.")
    for error in errors:
        print(error, file=sys.stderr)
    return errors



def open_calibrated_bus(robot):
    try:
        robot.bus.connect(handshake=False)
        import fcntl, termios
        fcntl.ioctl(robot.bus.port_handler.ser.fileno(), termios.TIOCEXCL)
        # 이 속성은 실제 모터의 보정 레지스터를 읽으므로 버스를 연 뒤에만 검사한다.
        if not robot.is_calibrated:
            raise RuntimeError("파일과 모터 보정이 불일치; 자동 보정 없이 중단")
    except BaseException:
        robot.bus.disconnect(disable_torque=False)
        raise


def main():
    args = parse_args()
    # 과거 중단 사유/해시와 새 진단에 묶인 1회 읽기 재개. live는 허가하지 않는다.
    session=DrySession(args.dry_session,live=args.live)
    session.begin(args)
    import device_preflight
    device_preflight.require()
    if args.max_seconds > 5 or args.fps != 30 or args.step_cap > 0.5 or args.max_deg_per_second > 20 or args.first_action_limit > 10 or args.load_limit > 200 or args.temperature_every > 5:
        raise SystemExit("v6 첫 시험 기준 완화 거부: 5초/30Hz/0.5도/20도초/첫10도/부하200/간격5")
    if args.max_seconds <= 0 or args.temperature_every <= 0 or args.fps <= 0:
        raise SystemExit("오류: 시간·온도 확인 간격·FPS는 양수여야 합니다")
    if not math.isfinite(args.max_seconds) or not math.isfinite(args.load_limit) or args.load_limit <= 0:
        raise SystemExit("유한한 시간/부하 상한 필요")
    if not math.isfinite(args.first_action_limit) or args.first_action_limit <= 0:
        raise SystemExit("유한한 양수 첫 목표 제한 필요")
    rate_config(args.fps, args.step_cap, args.max_deg_per_second)

    # cuSPARSELt는 사용자가 Jetson에서 지정한 0.6.3.2 경로를 환경변수로 전달한다.
    checkpoint = Path(args.checkpoint).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    if not checkpoint.is_dir():
        raise SystemExit("체크포인트 폴더가 없음: %s" % checkpoint)
    if output.exists():
        raise SystemExit("출력 폴더가 이미 있어 덮어쓰지 않음: %s" % output)
    metadata = inspect_checkpoint(checkpoint)
    print(json.dumps({k:metadata[k] for k in ("checkpoint_kind","camera_mapping","normalization_files")}, ensure_ascii=False))
    if metadata["checkpoint_kind"] != "SO101_FINETUNED_CANDIDATE":
        raise RuntimeError("live 거부: base 또는 파인튜닝 여부 UNVERIFIED")

    # 늦은 import: --help와 사전 경로 확인은 로봇 SDK를 불러오지 않는다.
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.cameras import ColorMode
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from lerobot.utils.constants import HF_LEROBOT_CALIBRATION, ROBOTS

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA를 사용할 수 없어 실행을 중단함")
    calibration_dir = (Path(args.calibration_dir).expanduser() if args.calibration_dir
                       else HF_LEROBOT_CALIBRATION / ROBOTS / "so_follower")
    calibration_file = calibration_dir / (args.robot_id + ".json")
    if not calibration_file.is_file():
        raise FileNotFoundError("보정 파일이 없어 진행하지 않음: %s" % calibration_file)
    print("관절 순서:", JOINTS, "설정 단위: degree×5 / gripper 0~100; 학습 보정 대응: UNVERIFIED")
    print("현재 보정 파일 SHA256:", hashlib.sha256(calibration_file.read_bytes()).hexdigest())
    if args.live or args.dry_session:
        verify_units(args.unit_evidence, checkpoint, calibration_file)
        if not args.home_state:
            raise RuntimeError("경고: home 근거 없음 — live 거부")
    home = json.loads(args.home_state.expanduser().read_text()) if args.home_state else None
    if (not home or isinstance(home.get("tolerance_deg"), bool) or not isinstance(home.get("tolerance_deg"), (float, int)) or not 0 < home["tolerance_deg"] <= 5):
        raise Hold("HOME 허용범위 5도 조건 미확인/완화 거부")

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    policy = SmolVLAPolicy.from_pretrained(str(checkpoint), local_files_only=True).to("cuda").eval()
    if policy.config.chunk_size != 50 or policy.config.n_action_steps != 50:
        raise RuntimeError("체크포인트 chunk_size/n_action_steps가 50/50이 아님")
    preprocess, postprocess = make_pre_post_processors(
        policy.config,
        str(checkpoint),
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
    )

    saved_model_config=json.loads((checkpoint/"config.json").read_text())
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
    devices = dict(end_effector=args.end_effector_camera,
                   ceiling_vertical=args.ceiling_vertical_camera,
                   ceiling_oblique=args.ceiling_oblique_camera)
    for name, value in devices.items():
        print("카메라 실제 입력:", name, "=", value)
    # 공식 0.6.0 Cv2Backends.V4L2=200. ANY는 장치 경로를 FFmpeg로 열 수 있다.
    camera_cfgs = make_v4l2_configs(OpenCVCameraConfig, devices)
    config_kwargs = dict(
        port=args.port,
        id=args.robot_id,
        cameras=camera_cfgs,
        max_relative_target=2.0,
        disable_torque_on_disconnect=False,
        use_degrees=True,
    )
    if args.calibration_dir:
        config_kwargs["calibration_dir"] = calibration_dir
    robot = SO101Follower(SO101FollowerConfig(**config_kwargs))
    if set(robot.calibration) != set(JOINTS):
        raise RuntimeError("보정 파일의 6축 이름이 불완전함; 자동 보정 없이 중단")

    output.mkdir(parents=True, exist_ok=False)
    connected_bus = False
    connected_cameras = []
    action_attempted = False
    started = False
    frame_watch = FrameWatch()
    from collections import deque
    recent_clips = deque(maxlen=20)
    # 정책 스레드에는 robot/bus를 전달하지 않는다. 패킷 보호는 소유 스레드만 허용.
    permission = PacketPermission(robot.bus.port_handler, allow_goal=False).install()
    robot._v6_permission = permission
    def monitor():
        session.check()
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, False)
        read_health(robot.bus, load_limit=args.load_limit)
        session.monitor_count += 1
    chunk_count = 0
    run_started = None
    csv_file = None
    run_error = None
    phase_metrics=[]
    try:
        # SO101Follower.connect()는 calibration/configure/torque_disabled 경로를 실행한다.
        # 이 시험은 그 쓰기를 피하기 위해 bus만 handshake 없이 열고 카메라는 개별 연결한다.
        device_preflight.require()
        print("관절 버스 연결 및 보정 읽기 시작", flush=True)
        open_calibrated_bus(robot)
        connected_bus = True
        print("관절 버스 연결 및 보정 읽기 완료", flush=True)
        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, args.live)
        read_health(robot.bus, starting=True, load_limit=args.load_limit)
        for name in CAMERAS:
            camera = robot.cameras[name]
            connected_cameras.append(camera)
            print("카메라 연결 시작: %s (V4L2)" % name, flush=True)
            camera.connect()
            print("카메라 연결 완료: %s" % name, flush=True)
        if not robot.is_connected:
            raise RuntimeError("버스와 카메라가 모두 연결되지 않음")

        torque, modes = read_motor_state(robot.bus)
        motor_mode_gate(torque, modes, args.live)
        print("모터 상태(읽기만): torque=%r mode=%r" % (torque, modes), flush=True)
        if not args.live and any(value != 1 for value in torque.values()):
            print("dry-run: 토크가 꺼진 상태로 관측·계산만 진행합니다. 토크는 변경하지 않습니다.", flush=True)
        temperatures = read_health(robot.bus, starting=True, load_limit=args.load_limit)

        state, observation, frames = build_observation(robot, torch, np)
        vector(state)
        frame_watch.check({k: v.tobytes() for k, v in frames.items()})
        if args.live or args.dry_session:
            home_gate(state, home)
        elif not home:
            print("경고: home 미확인 — UNVERIFIED; live 차단")
        for name, frame in frames.items():
            Image.fromarray(frame, mode="RGB").save(str(output / (name + "_start.png")))

        with (output/"state_start.json").open("x",encoding="utf-8") as f:
            json.dump(dict(joint_names=list(JOINTS),state=state,temperatures=temperatures,
                           torque=torque,modes=modes,source="CURRENT_READ_ONLY"),f,ensure_ascii=False,indent=2)
        csv_path = output / "chunks.csv"
        csv_file = csv_path.open("x", encoding="utf-8", newline="")
        writer = csv.writer(csv_file)
        writer.writerow([
            "chunk", "task", "joint", "unit", "state_at_inference", "first_action",
            "delta_first_action", "inference_seconds", "chunk_execution_seconds",
            "temperature_max_c", "mode", "first_action_over_2_unit_warning", "configured_hz", "observed_hz", "step_cap_deg", "max_deg_per_second",
        ])
        one_chunk_dry = args.dry_run or not args.live

        if args.live:
            print("실제 동작 모드입니다. 주변을 비우고 비상 정지 수단을 준비한 뒤 LIVE를 입력하세요.")
            confirmation = monitored_inference(lambda: input("확인 문자열: "), monitor,
                deadline=time.monotonic() + 60, timeout=60)
            if confirmation.strip() != "LIVE":
                raise RuntimeError("사용자 확인이 일치하지 않아 모터 명령 없이 종료")
            fresh, _, _ = build_observation(robot, torch, np)
            home_gate(fresh, home)
            read_health(robot.bus, starting=True, load_limit=args.load_limit)
            state = fresh
        run_started = time.perf_counter()
        previous_goal = state[:]

        while time.perf_counter() - run_started < args.max_seconds:
            capture_start=time.perf_counter()
            observed = build_observation(robot, torch, np)
            phase_metrics.append(dict(capture_seconds=time.perf_counter()-capture_start))
            state, packed, _ = observed
            home_gate(state,home)
            with (output/("state_inference_%d.json"%chunk_count)).open("x",encoding="utf-8") as f:
                json.dump(dict(state=state,task=args.task),f,ensure_ascii=False)
            # GPU 동기화도 정책 전용 스레드 안에서 처리하고 메인은 serial 감시를 계속한다.
            def predict_only():
                torch.cuda.synchronize()
                result = infer_with_saved_amp(
                    lambda:infer_chunk(policy,preprocess,postprocess,dict(packed,task=[args.task]),torch),torch,saved_model_config)
                torch.cuda.synchronize()
                return result
            infer_started = time.perf_counter()
            remaining = args.max_seconds - (time.perf_counter() - run_started)
            phase_metrics[-1]["remaining_before_inference"]=remaining
            phase_metrics[-1]["inference_wait_budget"]=min(remaining,50/args.fps)
            actions = monitored_inference(lambda:gpu_worker.call(predict_only,label="current_observation_chunk"), monitor,
                deadline=time.monotonic() + max(0, remaining), timeout=50.0 / args.fps)
            infer_seconds = time.perf_counter() - infer_started
            first = actions[0]
            for action in actions:
                vector(action)
            first_action_gate(first, state, args.first_action_limit)
            deltas = [first[i] - state[i] for i in range(6)]
            if any(abs(deltas[i]) >= 2.0 for i in range(5)):
                print("경고: 첫 5축 action 중 현재 관절값에서 2도 이상 벗어나는 항목이 있음; send_action 제한이 2도로 자릅니다.")
            if abs(deltas[5]) >= 2.0:
                print("경고: 그리퍼 첫 action이 현재값에서 2 percentage-point 이상 벗어남; send_action 제한이 2로 자릅니다.")

            max_temp = max(read_health(robot.bus, load_limit=args.load_limit).values())
            raw_check = robot.get_observation()
            frame_watch.check({k: raw_check[k].tobytes() for k in CAMERAS})
            this_chunk_start = time.perf_counter()
            executed = 0
            if args.live and time.perf_counter() - run_started < args.max_seconds:
                for step, action in enumerate(actions):
                    if time.perf_counter() - run_started >= args.max_seconds:
                        break
                    if step % args.temperature_every == 0:
                        temps = read_health(robot.bus, load_limit=args.load_limit)
                        max_temp = max(temps.values())
                        if step > 0:
                            raw_check = robot.get_observation()
                            frame_watch.check({k: raw_check[k].tobytes() for k in CAMERAS})
                    now_state = robot.bus.sync_read("Present_Position")
                    measured = vector([float(now_state[j]) for j in JOINTS])
                    if not action_attempted:
                        home_gate(measured, home)
                        first_action_gate(action, measured, args.first_action_limit)
                    # 매 전송 직전 토크/모드를 확인. OFF 상태를 Goal 쓰기로 재활성화하지 않음.
                    check_stop_record()
                    torque, modes = read_motor_state(robot.bus)
                    motor_mode_gate(torque, modes, True)
                    if any(abs(action[i] - measured[i]) > 10 or abs(action[i] - previous_goal[i]) > 10 for i in range(6)):
                        raise Hold("큰 점프: clip으로 감추지 않고 중단")
                    target = safe_target(action, measured, previous_goal, args.step_cap, args.fps, args.max_deg_per_second)
                    recent_clips.append(any(abs(target[i] - action[i]) > 1e-6 for i in range(6)))
                    if len(recent_clips) == 20 and sum(recent_clips) / 20 > 0.5:
                        raise Hold("정책-제어 불일치: 누적 clip 비율 초과")
                    if time.perf_counter() - run_started >= args.max_seconds:
                        raise Hold("전송 직전 전체 시간 상한")
                    action_attempted = True
                    step_start = time.perf_counter()
                    with permission.goal_once():
                        sent = robot.send_action({joint + ".pos": target[i] for i, joint in enumerate(JOINTS)})
                    previous_goal = vector([float(sent[j + ".pos"]) for j in JOINTS])
                    executed += 1
                    # 지연 후 연속 전송을 막고 각 목표 사이에 최소 1/fps 간격을 둔다.
                    target_time = step_start + 1.0 / args.fps
                    delay = target_time - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
            execution_seconds = time.perf_counter() - this_chunk_start if args.live else float("nan")
            writer = csv.writer(csv_file)
            for i, joint in enumerate(JOINTS):
                writer.writerow([
                    chunk_count, args.task, joint,
                    "percent_0_100" if i == 5 else "degree",
                    state[i], first[i], deltas[i], "%.6f" % infer_seconds,
                    "%.6f" % execution_seconds if args.live else "NOT_RUN",
                    "%.2f" % max_temp, "live" if args.live else "dry-run",
                    abs(deltas[i]) >= 2.0,
                    args.fps, executed / execution_seconds if args.live and execution_seconds > 0 else "NOT_RUN",
                    args.step_cap, args.max_deg_per_second,
                ])
            csv_file.flush()
            chunk_count += 1
            print(json.dumps({
                "chunk": chunk_count - 1,
                "task": args.task,
                "inference_seconds": round(infer_seconds, 4),
                "executed_steps": executed,
                "50_step_execution_seconds": round(execution_seconds, 4) if args.live and executed == 50 else "NOT_RUN_OR_PARTIAL",
                "temperature_max_c": max_temp,
                "output": str(output).replace(str(Path.home()),"~"),
            }, ensure_ascii=False), flush=True)
            if one_chunk_dry:
                break

    except KeyboardInterrupt:
        run_error=dict(kind="KeyboardInterrupt",reason="Ctrl+C; 추가 목표/토크 쓰기 없음")
        raise SystemExit(130)
    except Exception as exc:
        run_error=dict(kind=type(exc).__name__,reason=str(exc).replace(str(Path.home()),"~"))
        if phase_metrics and "infer_started" in locals():phase_metrics[-1]["wait_elapsed_until_error"]=time.perf_counter()-infer_started
        print(json.dumps(dict(status="HOLD",error=run_error),ensure_ascii=False),flush=True)
        raise SystemExit(2)
    finally:
        cleanup_errors=close_session(robot, connected_cameras, args.live, action_attempted, connected_bus)
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
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    print("완료: mode=%s chunks=%d output=%s" % ("live" if args.live else "dry-run", chunk_count, str(output).replace(str(Path.home()),"~")))


if __name__ == "__main__":
    main()
