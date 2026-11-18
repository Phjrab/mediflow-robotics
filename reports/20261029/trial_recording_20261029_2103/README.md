# 시험 녹화 검수 결과

원본: Supermassive111/test10-291_20261029_205247
Hub revision: 831c150add1779848470df8aa42e26071290210c

## 수행 결과

- 공개 Hub 데이터셋 존재 및 메타데이터·Parquet·영상 3개 다운로드 확인. 원본 수정 없음.
- LeRobot 형식 v3.0, 2 episodes, 1053 frames, 30 fps.
- episode 0: 416 frames / 13.8667초. episode 1: 637 frames / 21.2333초. 합계 35.1초는 녹화된 프레임 길이이며 전체 벽시계 세션 시간은 아님.
- 따라서 UI Session Duration 0s는 저장 데이터 길이를 나타내지 않음. UI 표시 원인은 미확인.
- state/action float32 각 6차원; 순서 shoulder_pan.pos, shoulder_lift.pos, elbow_flex.pos, wrist_flex.pos, wrist_roll.pos, gripper.pos.
- task: Put medicine bottle A into the blue basket (메타 task 및 에피소드 task 목록).
- timestamp 간격 이상 0 (30 fps, 1ms 허용), state/action NaN/Inf 0.

| 실제 데이터 키 (observation.images. 접두어) | 해상도 | MP4 fps | 디코딩 ep0/ep1 | 학습용 이름 → 모델 키 |
|---|---|---|---|---|
| ceiling_vertical | 640×480 | 30 | 416 / 637 | top → camera2 |
| ceiling_oblique | 640×480 | 30 | 416 / 637 | oblique → camera3 |
| end_effector | 640×480 | 30 | 416 / 637 | wrist → camera1 |

카메라별 에피소드 중간 PNG 6개와 수치 결과 inspection.json을 같은 폴더에 저장했다.
두 Astra 중간 프레임에 LIVE 글자·점검 박스 없음. 전체 영상 모든 프레임의 문자 검사를 한 것은 아님.
Astra에서는 다른 카메라의 파란 로봇이 초록색으로 보이는 색상 불일치가 확인됨. 원인(RGB/BGR 등)은 미확정이며 본 녹화 전 수정/확인 권장.
연속 동일 디코딩 이미지: top 21쌍, oblique 27쌍, wrist 68쌍. 정지 장면일 수 있으므로 이 수치만으로 카메라 멈춤으로 판정하지 않음.
실제 시연 성공·바구니 투입 여부는 중간 프레임으로 판정하지 않았다.

## Linux 0.6.0 재로딩

실제 Linux 재로딩: NOT_RUN (원격 Linux host 현재 이용 불가). 노트북에는 torch/lerobot 실행 환경이 없음.
배포 wheel의 LeRobot 0.6.0 소스만 확인: datasets/dataset_metadata.py:58 CODEBASE_VERSION=v3.0; datasets/lerobot_dataset.py:47 이하 root/revision/video_backend 인자; datasets/video_utils.py:94 pyav 디코딩 경로.
따라서 데이터 형식 변환은 소스 기준 불필요할 것으로 판단하지만, 실제 학습 환경에서 샘플 로딩 성공은 아직 미확인이다.
기존 학습 래퍼가 canonical 키만 요구하면 원본을 유지한 별도 파생본에 기존 new-recording 변환을 사용해 위 이름만 정규화한다. 모델 rename_map은 기존 단일 설정을 유지한다.

Linux 프로젝트 폴더에서 실행할 준비된 명령(실행하지 않음):

```bash
.local/venv-smolvla060/bin/python - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download
from lerobot.datasets.lerobot_dataset import LeRobotDataset
repo = 'Supermassive111/test10-291_20261029_205247'
revision = '831c150add1779848470df8aa42e26071290210c'
root = Path.home() / 'so101-data' / 'raw' / 'test10-291_20261029_205247_hubcheck'
if root.exists():
    raise SystemExit('REFUSED: destination exists; choose a new directory')
snapshot_download(repo_id=repo, repo_type='dataset', revision=revision, local_dir=root)
dataset = LeRobotDataset(repo, root=root, revision=revision, video_backend='pyav')
sample = dataset[0]
print({'episodes': dataset.num_episodes, 'frames': dataset.num_frames,
       'images': {k: list(v.shape) for k, v in sample.items() if k.startswith('observation.images.')},
       'state': list(sample['observation.state'].shape), 'action': list(sample['action'].shape),
       'task': sample.get('task')})
PY
```

기대 결과: 2 episodes / 1053 frames / 이미지 3개 각 [3,480,640] / state [6] / action [6] / task 일치. 실패하면 예외 요약을 확인한 뒤 변환 필요 여부를 판단한다.

## 미실행

Linux 공식 로더 실제 재로딩, 학습, 카메라/serial 열기, 로봇 구동, 녹화, 원본 변경, push 모두 미실행. 추가 Jetson 복사는 공개 Hub 사본을 사용할 수 있으므로 불필요.
