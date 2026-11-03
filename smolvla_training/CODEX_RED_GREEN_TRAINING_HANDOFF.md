# SO-101 SmolVLA 빨강·초록 학습 — 다른 컴퓨터 Codex 인계 프롬프트

> **2026-11-03 추가:** 사용자가 새 파랑 학습 자료도 요청했다. 먼저 [파랑 추가 인계](BLUE_DATA_HANDOFF_20261103.md)를 읽는다. 아래 red/green-only 범위는 11월 2일 기록이며, 파랑 추가 준비는 새 manifest와 추가 인계의 우선 지침을 따른다. 기존 빨강/초록 데이터·체크포인트는 보존한다.

아래 내용을 새 컴퓨터의 Codex에 전달한다. 기준일은 2026-11-02다. 과거 보고와 현재 승인 범위를 구분하고, 확인하지 않은 상태는 미확인으로 적는다.

## 지금 사용자가 원하는 작업

약통 **A를 빨간/초록 바구니에 넣는 언어 조건부 SmolVLA 정책 하나**를 준비하고 GPU PC에서 학습한다. **이번 기본 학습에는 blue 데이터셋을 넣지 않는다.** blue 점검 자료는 참고 이력으로만 보존했다.

현재 저장소: `https://github.com/Phjrab/mediflow-robotics`
인계 폴더: `smolvla_training/`
업로드 브랜치: `codex/smolvla-handoff-20261102`

사용자가 붙여넣은 이 프롬프트를 실행 요청으로 받은 경우 아래 순서대로 진행한다. 코드·데이터 준비와 mock 검증은 자율 진행한다. 실제 학습은 GPU/환경/데이터 재로딩 확인 후 시작하며, 새 컴퓨터의 로컬 GPU만 사용한다. 유료 클라우드 사용·시스템 드라이버 변경·재부팅은 자동으로 하지 않는다. 로그와 재개 명령을 남긴다.

## 절대 유지할 조건

- 원본 데이터·체크포인트 삭제/덮어쓰기 금지. 변환/필터/병합은 새 디렉터리에 생성한다.
- 로봇·모터·serial·카메라 열기, 녹화, shadow/live 실행 금지. 학습 PC의 GPU 확인과 저장 영상 디코딩은 가능하다.
- 비밀번호·토큰·실제 장치 주소·계정은 파일/로그/커밋에 기록하지 않는다. Hub 인증이 필요하면 환경변수/안전한 인증 경로를 사용한다.
- push/force push는 이 새 작업에서 사용자가 별도 요청할 때만 한다. 이번 인계 자료 업로드에 대한 승인을 이후 모든 작업의 push 승인으로 확대하지 않는다.
- 실패 회차라고 기록된 것만 제외한다. 약통을 세워 넣었는지/떨궜는지는 추가 제외 기준이 아니다.
- 데이터 점검 결과·학습 완료·실로봇 성공을 구분한다. 이 데이터로 로봇이 성공할지 추정하지 않는다.

## 현재 사용할 데이터 — Hub에 공개 업로드 및 파일 검수 완료

### 빨강

- repo: `Supermassive111/a_to_red_20261102_201029`
- revision: `c14477c22cb0cf3b27599a32b96222631380720e`
- 원본: 10 episodes, 4,628 frames.
- task: `Put medicine bottle A into the red basket`
- 사용자 메모: **2번째 에피소드 실패 → 제외**.
- 번호는 1부터 센 순서다. LeRobot **episode_index=1**을 제외한다.
- 제외 회차 길이: 685프레임, 약 22.83초. 이 회차는 red에서 가장 길며, 길이로 실패를 재판정하지 않는다.
- 남길 원본 인덱스: `[0,2,3,4,5,6,7,8,9]`.
- 제외 후 예상: **9 episodes, 3,943 frames**.

### 초록

- repo: `Supermassive111/a_to_green_20261102_201804`
- revision: `ecc9d395cdce6bcc8a44ffbd6840ebe2f2d2466d`
- 원본: 10 episodes, 5,510 frames.
- task: `Put medicine bottle A into the green basket`
- **제외 없음.** 원본 인덱스 `[0,1,2,3,4,5,6,7,8,9]` 전부 포함.
- 사용자 메모의 5·6번째(index 4·5)는 약통 떨굼이지만 **참고만 하고 포함**한다.
- 해당 길이: index 4는 502프레임/16.73초, index 5는 456프레임/15.20초.

### 두 색을 합쳤을 때

- 원본 합: 20 episodes, 10,138 frames.
- red index 1만 제외한 목표: **19 episodes, 9,453 frames**.
- 서로 다른 데이터셋의 episode_index는 각각 0부터 시작한다. 합친 뒤 번호는 새로 매겨질 수 있으므로 원본 repo/revision/episode_index → 새 episode_index 대응표를 남긴다.
- task는 원래 문자열을 유지한다. 모든 에피소드를 하나의 문장으로 덮어쓰면 안 된다.
- 서로 다른 task/바구니를 구분하도록 언어 입력을 사용하는 SmolVLA로 학습한다. ACT는 언어 조건부 비교 정책으로 취급하지 않는다.

## 확인된 저장 형식과 검수 결과

- LeRobot 데이터 형식 **v3.0**. 설치할 Python 패키지 버전은 **lerobot==0.6.0**이다. 두 버전 표기는 다른 개념이다.
- 영상은 AV1 MP4, 640×480, 30fps, RGB 3채널. 각 카메라의 실제 디코딩 프레임 수가 데이터 행 수와 일치했다.
- 실제 카메라 키:
  - `observation.images.end_effector`: 손목 USB 카메라.
  - `observation.images.ceiling_vertical`: 천장 수직 Astra.
  - `observation.images.ceiling_oblique`: 천장 사선 RealSense.
- state/action은 각각 float32 6차원. names/순서: `shoulder_pan.pos`, `shoulder_lift.pos`, `elbow_flex.pos`, `wrist_flex.pos`, `wrist_roll.pos`, `gripper.pos`.
- red/green/blue 총 30회/14,149프레임을 검수했다. 각 task가 모든 데이터 행과 에피소드 메타데이터에서 정확히 일치했다.
- 디코딩 오류, 빈 영상, 단색/검정 프레임 후보, 프레임 수 불일치, NaN/Inf, timestamp 간격 이상은 없었다.
- 완전히 같은 RGB 이미지가 연속 2프레임인 경우는 있다. 최대 run은 약 0.067초이며 1초 이상 반복 후보는 없었다. 정지 장면을 카메라 오류나 실패로 단정하지 않는다.
- 짧음 후보는 없었다. 사용한 기준은 2초 미만 또는 데이터셋 중앙 길이의 50% 미만이다.
- 이 검수는 저장 파일 형식/수치/디코딩 점검이며 실로봇 성공 판정이 아니다.
- 자세한 결과와 원본 번호는 `reports/three_color_audit/`, 전체 제외 목록은 `manifests/excluded_episodes.json`, 이번 두 색 범위는 `manifests/red_green_training_manifest.json`을 읽는다.

## 카메라 이름과 모델 입력 — 순서 바꾸지 말 것

프로젝트의 선택은 **wrist→camera1, top→camera2, oblique→camera3**이다. 물리 위치와 저장 이름의 대응은 사용자가 확인했다. 모델이 이 순서를 공식 지정했다고 주장하지 않는다.

저장 키를 그대로 쓴다면 실제 학습 rename 후보는 다음과 같다.

```json
{
  "observation.images.end_effector": "observation.images.camera1",
  "observation.images.ceiling_vertical": "observation.images.camera2",
  "observation.images.ceiling_oblique": "observation.images.camera3"
}
```

방향은 **source=데이터셋 관측 키 → 모델 입력 키**다. action/state/언어 task를 rename하지 않는다.
기존 `so101_code/configs/vla_train.yaml`에는 canonical 이름 wrist/top/oblique의 동일한 프로젝트 순서가 있다. 실제 키를 canonical로 바꾼 파생본을 만드는 경우에만 그 설정을 그대로 쓴다. 이미 canonical로 바꿨는데 원래 키를 다시 rename하는 이중 변환은 하지 않는다.
학습과 이후 Jetson 실행은 같은 대응을 사용해야 한다. `--rename_map` 등 실제 옵션 이름은 새 환경의 **lerobot-train --help와 설치 소스로 확인한 것만** 사용한다.

관절 단위는 값 범위로 추정하지 않는다. 저장 state/action을 학습 준비 중 임의 단위 변환하지 않는다. 실제 녹화 설정의 단위와 RobotController 입력 단위가 일치한다는 증거는 이번 자료에서 확정하지 않았고, 따라서 이 자료로 live 실행을 승인하지 않는다.

## Astra 현재 상태 — 더 수정하지 않는다

**Astra 색 불일치가 알려져 있지만 현재 녹화 경로를 그대로 사용**하기로 사용자가 결정했다. 이 이유만으로 데이터셋이나 에피소드를 제외하지 않는다. RealSense와 손목 영상은 색이 정상이다.

원본 Astra 브리지:

```ini
ExecStart=/bin/bash -lc 'set -o pipefail; /opt/orbbec-openni2/bin/orbbec-rgb-pipe | /usr/bin/gst-launch-1.0 -q fdsrc fd=0 ! rawvideoparse width=640 height=480 framerate=30/1 format=rgb ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=/dev/video8 sync=false'
```

위 줄은 **재현용 기록이며 학습 PC에서 실행하지 않는다.** 원시 source는 RGB888/stride 처리였다. 전체 색 문제의 정확한 원인은 미확인이다. BGR 출력 후보는 Astra 영상 수신에 실패해 철회했고, 추가 drop-in을 제거해 기존 YUY2 영상으로 복귀했다. 그 후보를 다시 적용하지 않는다. 기록과 실행은 같은 Astra 경로를 써야 한다.

## 코드와 과거 학습의 상태 — 완전한 최신판으로 오해하지 말 것

- `so101_code/`는 공개 `gyeyeongjo-lgtm/so101-lelab-medicine-sorter`의 **ad9bc48** 기반 1~6단계 코드 사본이다. 데이터 검수/파생/세션 분리/학습 준비/mock 추론/평가 도구가 있다. 기존 안전 계층을 약화하거나 로봇 실행을 자동화하지 않는다.
- 노트북에서 만든 최신 녹화 안내서와 3색 검수·제외 파일을 이 인계 폴더에 추가했다.
- 이전 Linux fast-track의 최신 커밋은 보고상 **05d144f**였다. 현재 그 PC 연결이 없어 `vla_fast_train.py`, `vla_prepare_a_fast.py`, `check_new_recording.py` 등의 완전한 최신 원문을 가져오지 못했다. **이 저장소 사본이 그 커밋 전체를 포함한다고 가정하지 않는다.** 불완전한 소스 조각을 실행 파일로 만들지 않았다.
- 과거 **다른 장소의 A-v2**로 200 step 시험 학습과 체크포인트 재로딩/select_action 6차원 확인을 했고, 본학습은 1,763 step에서 사용자가 중단했다. 데이터/로그/200-step 체크포인트는 보존됐지만 이 인계에는 가중치가 없다. **새 빨강·초록 데이터의 학습 결과가 아니므로 재개/초기 정책으로 잘못 쓰지 않는다.**
- 새 데이터 학습은 `lerobot/smolvla_base`에서 시작한다. 과거 GPU는 RTX 3060이었지만 새 PC GPU/VRAM/드라이버는 **미확인**이다.
- 전체 회귀/189·260 테스트 차이/Gate 문서 분석은 재개하지 않는다. 이번에 추가하거나 바꾼 데이터 준비 코드만 필요한 mock/fixture 테스트를 한다.
- 검증된 기존 도구를 재사용한다. 현재 데이터 범위와 맞지 않는 강한 guard를 몰래 우회하지 말고, 공식 0.6.0 데이터 편집 API와 명시적인 새 경로를 이용한 최소 준비 코드를 만든다.

## 새 PC에서 수행 순서

1. `nvidia-smi`, Python 버전, 디스크 여유를 확인한다. torch CUDA 가용성과 GPU/VRAM을 확인한다. 드라이버 mismatch면 원인만 보고하고 시스템 변경 없이 데이터 준비까지만 한다.
2. 프로젝트 전용 venv를 만든다. `lerobot==0.6.0`과 smolvla/데이터·학습 의존성을 설치한다. 실제 제공 extras와 CUDA torch 조합은 패키지/새 PC 환경을 확인해서 선택한다. 시스템 Python/드라이버는 건드리지 않는다. 설치된 버전과 `lerobot-train --help`를 확인한다.
3. Hub의 위 **고정 revision**으로 raw 데이터를 Git 밖의 새 경로에 받는다. 예: `~/so101-data/raw/red_<revision>`와 `green_<revision>`. 기존 폴더를 덮어쓰지 않는다. 토큰을 커밋/로그에 넣지 않는다.
4. 공식 LeRobotDataset으로 두 원본을 실제 재로딩해 에피소드/프레임/task/3카메라/6차원을 확인한다. AV1 디코딩 문제가 있으면 PyAV backend를 확인한다. 패키지 버전 0.6.0 소스에는 v3.0과 PyAV 경로가 있음을 확인했지만, **새 PC에서의 실제 재로딩은 아직 안 했다.**
5. red index 1만 제외한 **새 파생본**을 만든다. green은 전부 포함한다. 설치 소스에 공식 `lerobot.datasets.dataset_tools.delete_episodes(dataset, episode_indices, output_dir, repo_id)`가 있는지 다시 확인해 사용한다. 이 API는 이름과 달리 새 데이터셋을 만드는 기능이다. 원본 경로를 출력으로 주지 않는다.
6. 공식 `merge_datasets(datasets, output_repo_id, output_dir, ...)`가 설치 0.6.0에 있는지 확인하고 red 파생본 + green 원본을 **또 다른 새 경로**에 합친다. 파일 재인코딩/통계를 포함한 실제 동작과 출력 경로를 확인한다. subset loader에 episodes만 지정하고 합치면 유틸이 그 subset을 무시할 수 있으므로 그것만으로 제외를 구현하지 않는다.
7. 병합본이 **19회/9,453프레임**, task 2개, 카메라 3개, state/action 6차원인지 공식 loader로 확인한다. 출처/index 재매핑과 원본 불변을 확인한다. 원본 global 통계가 그대로 남아 있지 않은지도 확인한다.
8. 촬영 세션별 확실한 train/eval 분리 증거는 없다. 임의 세션을 만들지 않는다. 전체 19회로 학습하는 경우 그 사실을 남기고 검증 성공률을 보고하지 않는다. 별도 실제 평가 기록은 없다.
9. **200 step 시험 학습**을 새 output_dir에서 한다. 후보: batch=4, num_workers=2, seed=101, device=cuda, W&B off, Hub 업로드 off. chunk_size 등은 base 기본값을 유지한다. OOM이면 batch를 절반씩 줄인다. 모든 CLI 옵션은 --help/소스 확인 후에만 쓴다.
10. 체크포인트를 재로딩해 저장 데이터 샘플 1개로 정책 출력 차원과 유한값을 확인한다. 실제 로봇/카메라는 열지 않는다. step 시간·최대 VRAM을 보고한다. 이 확인은 로봇 성공 판정이 아니다.
11. 시험 성공 시 같은 batch/데이터/task/rename으로 **20,000 step**, 5,000 step마다 저장하도록 새 본학습을 시작한다. 체크포인트 폴더를 덮어쓰지 않는다. nohup/tmux 등으로 로그를 남기고 output 경로·PID/세션·재개 명령을 알려준다. 중단 시 실제 생성된 마지막 체크포인트에서만 재개하며 존재하지 않는 checkpoint를 가정하지 않는다.

## 최종 보고 형식

각 단계는 수행한 것/확인 수치/실패 원인만 간결하게 보고한다. 끝에는 다음을 구분한다.

1. 수행 완료: 환경·다운로드·파생·병합·loader 확인·학습 단계 중 실제 완료한 것.
2. 실행 중/다음: 학습 프로세스, 로그, 체크포인트, 예상 시간(실측 기준), 정확한 재개 명령.
3. 미실행·차단: 장치 실행/실로봇 성공/평가/미접속 자료 등. 추측하거나 PASS로 꾸미지 않는다.

사용자가 중간에 답하지 않아도 위 범위에서 진행하되, 원본 덮어쓰기·유료 자원·시스템 변경·로봇 실행으로 범위를 넓히지 않는다.
