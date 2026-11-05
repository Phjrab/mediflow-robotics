"""파일만 읽는 소프트웨어 점검. 모델·GPU·카메라·로봇·serial 실행 없음."""
import argparse
import hashlib
import json
from pathlib import Path
import policy_api as api
from observation_contract import CONFIG,CHECKPOINT,load_contract,saved_mapping

ROOT = Path(__file__).resolve().parent


def inspect_software(checkpoint=CHECKPOINT, config_path=CONFIG):
    contract = load_contract(config_path)
    mapping = saved_mapping(checkpoint)
    metadata = api.inspect_checkpoint(Path(checkpoint))
    if metadata['checkpoint_kind'] != 'SO101_FINETUNED_CANDIDATE':
        raise ValueError('파인튜닝 체크포인트 근거 미확인')
    model = json.loads((Path(checkpoint) / 'config.json').read_text(encoding='utf-8'))
    if any(model['input_features'][key]['shape'] != [3, 256, 256] for key in mapping.values()):
        raise ValueError('모델 영상 입력 형식 불일치')
    return dict(status='SOFTWARE_CONFIG_PASS', hardware_ready=False, mode='OFFLINE_ONLY',
                serial_stop_present=(ROOT / 'SERIAL_STOP.json').is_file(),
                serial_stop_scope='THIS_COPY_ONLY_NOT_REMOTE_ROBOT_STATUS',
                model_weights_file_present=(Path(checkpoint)/'model.safetensors').is_file(),
                model_loading='NOT_RUN_METADATA_ONLY',
                camera_config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                camera_paths=contract['cameras'], warmup_seconds=contract['warmup_seconds'],
                source_resolution=[640, 480, 3], model_resolution=[3, 256, 256],
                camera_mapping=mapping, rename_application='SAVED_PREPROCESSOR_EXACTLY_ONCE',
                normalization_files=metadata['normalization_files'], state_dimension=6, action_dimension=6,
                hardware_execution='NOT_RUN', policy_inference='NOT_RUN',
                unverified=['현재 관절 상태', '실기 관절 단위/보정', '현장 구도', '단일 물리 제어 경로'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=CHECKPOINT)
    parser.add_argument('--camera-config', type=Path, default=CONFIG)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = inspect_software(args.checkpoint, args.camera_config)
    if args.out:
        target = args.out.resolve()
        if (ROOT / 'reports').resolve() not in target.parents:
            raise ValueError('보고서는 도구의 reports 아래에만 저장')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(status=result['status'], hardware_ready=False, serial_stop_present=result['serial_stop_present'],
                          serial_stop_scope=result['serial_stop_scope'],model_weights_file_present=result['model_weights_file_present'],
                          camera_mapping=result['camera_mapping'], warmup_seconds=result['warmup_seconds']), ensure_ascii=False))


if __name__ == '__main__':
    main()
