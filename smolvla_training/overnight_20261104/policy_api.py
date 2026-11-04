"""기존 v4의 순수 검증·전처리/후처리 호출만 추출. 장치 실행 함수 없음."""
import hashlib
import json
import math
import struct
from pathlib import Path

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
