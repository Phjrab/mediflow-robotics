"""장치 없는 관측 계약. 경로는 공통 설정, 이름 변환은 저장 전처리만 사용한다."""
import json
import math
import time
from pathlib import Path
import policy_api as api

CONFIG = Path(__file__).resolve().parent / 'vla_observation_config.json'
CHECKPOINT = Path.home() / 'smolvla_model/pretrained_model'


def load_contract(path=CONFIG):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or value.get('rename_source') != 'checkpoint.policy_preprocessor.json':
        raise ValueError('관측 스키마 또는 rename 근거 불일치')
    for name, expected in [('width', 640), ('height', 480), ('channels', 3), ('fps', 30)]:
        if type(value.get(name)) is not int or value[name] != expected:
            raise ValueError('학습 관측 형식 불일치: ' + name)
    warm = value.get('warmup_seconds')
    if value.get('color_mode') != 'rgb' or isinstance(warm, bool) or not isinstance(warm, (int, float)) or not math.isfinite(warm) or not 3 <= warm <= 10:
        raise ValueError('RGB 또는 최소3초 예열 조건 불일치')
    cameras = value.get('cameras', {})
    if not isinstance(cameras, dict) or set(cameras) != set(api.CAMERAS) or not all(isinstance(x, str) for x in cameras.values()) or len(set(cameras.values())) != 3:
        raise ValueError('카메라 키 누락 또는 중복 경로')
    for role, device in cameras.items():
        if not isinstance(device, str) or '..' in device.split('/'):
            raise ValueError('장치 경로 형식 오류')
        if role == 'ceiling_vertical':
            if device != '/dev/video8':
                raise ValueError('Astra loopback 경로 미확인')
        elif not device.startswith('/dev/v4l/by-id/') or not device.endswith('video-index0'):
            raise ValueError('안정된 영상 카메라 경로 필요')
    return value


def warm_preview(response, reader, seconds, clock=None):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 3 <= seconds <= 10:
        raise ValueError('최소3초 예열 조건 불일치')
    clock = clock or time.monotonic
    started = clock()
    while clock() - started < seconds:
        reader(response)
    return reader(response)


def saved_mapping(checkpoint=CHECKPOINT):
    checkpoint = Path(checkpoint)
    config = json.loads((checkpoint / 'config.json').read_text(encoding='utf-8'))
    pre = json.loads((checkpoint / 'policy_preprocessor.json').read_text(encoding='utf-8'))
    mappings = [step['config']['rename_map'] for step in pre['steps'] if step.get('registry_name') == 'rename_observations_processor']
    if len(mappings) != 1:
        raise ValueError('저장 rename은 정확히 하나여야 함')
    mapping = mappings[0]
    sources = {'observation.images.' + name for name in api.CAMERAS}
    targets = {name for name in config['input_features'] if name.startswith('observation.images.')}
    if set(mapping) != sources or set(mapping.values()) != targets or len(set(mapping.values())) != 3:
        raise ValueError('저장 rename 방향·키 누락·중복 오류')
    if config['input_features'].get('observation.state', {}).get('shape') != [6]:
        raise ValueError('모델 state 6차원 불일치')
    return mapping


def display_mapping(checkpoint=CHECKPOINT):
    return {source.rsplit('.', 1)[-1]: target.rsplit('.', 1)[-1] for source, target in saved_mapping(checkpoint).items()}


def validate_frames(frames, contract):
    if set(frames) != set(contract['cameras']):
        raise ValueError('데이터 카메라 키 불일치; 모델 키를 미리 적용하지 말 것')
    expected = (contract['height'], contract['width'], contract['channels'])
    for role, frame in frames.items():
        if tuple(frame.shape) != expected or str(frame.dtype) != 'uint8':
            raise ValueError('raw RGB uint8 관측 형식 오류: ' + role)
    return frames


def pack_observation(frames, state, task, torch, contract):
    validate_frames(frames, contract)
    state = api.vector(list(state))
    if task not in api.COLOR_TASKS.values():
        raise ValueError('학습에 없는 task 또는 빈 문장')
    # source key 그대로 전달한다. saved_mapping()은 검증/표시용이며 여기서 적용하지 않는다.
    observation = {'observation.state': torch.tensor([state], dtype=torch.float32), 'task': [task]}
    for role, frame in frames.items():
        observation['observation.images.' + role] = torch.from_numpy(frame.copy()).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    return observation
