# SO-ARM101 ACT A/B/C 학습 완료 보고서

생성 시각: 2026-09-29 (KST)

## 완료 상태

| 모델 | 작업 | 스텝 | 첫 loss | 최종 loss | 최저 loss | 최종 체크포인트 |
|---|---|---:|---:|---:|---:|---|
| ACT-C | C 약통 → C 바구니 | 10,000 | 14.920 | 0.209 | 0.209 | `work/lerobot_outputs/act_c_3cam_10000_20260928_163839/checkpoints/010000/pretrained_model` |
| ACT-B | B 약통 → B 바구니 | 10,000 | 15.335 | 0.232 | 0.225 | `work/lerobot_outputs/act_b_3cam_10000_20260928_214354/checkpoints/010000/pretrained_model` |
| ACT-A | A 약통 → A 바구니 | 10,000 | 15.682 | 0.211 | 0.210 | `work/lerobot_outputs/act_a_3cam_10000_20260928_214354/checkpoints/010000/pretrained_model` |

세 학습 모두 정상 종료되었고 최종 10,000스텝 체크포인트가 저장됐다. 각 최종 모델은 234개 state-dict 텐서와 51,668,614개 state-dict 원소(학습 파라미터 51,597,190개)를 포함하며 NaN/Inf는 없다. 입력은 다음 네 개로 동일하다.

- `observation.images.ceiling_vertical`
- `observation.images.ceiling_oblique`
- `observation.images.end_effector`
- `observation.state`

## 최종 모델 SHA-256

- ACT-C: `27b114565b0281e3113a25acb618b2f4677f689ef2ca5eb5cc0cc0e5329afe0b`
- ACT-B: `8ed76e412ad73bed421e83061db097815bbd6c33e340e3ba973530a78449c6fe`
- ACT-A: `720ba1b36eb8aa5d8837cb641973c819dc5dba03b8a195819d6c7c7cfc9fede5`

## W&B 기록

프로젝트 이름은 `mediflow-so101-act`이며 모델별 run 이름은 다음과 같이 분리했다.

- `ACT-C__medicine-c-to-basket-c__3cam__10k`
- `ACT-B__medicine-b-to-basket-b__3cam__10k`
- `ACT-A__medicine-a-to-basket-a__3cam__10k`

세 offline run은 2026-09-29 12:40 KST에 W&B 웹 프로젝트로 모두 동기화됐다. 체크포인트 자체는 W&B artifact로 중복 저장하지 않았다.

- ACT-C: `https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/j4qkma86`
- ACT-B: `https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/pxg9ixpi`
- ACT-A: `https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/jcdrgxq2`

## 해석과 다음 단계

세 모델 모두 안정적으로 수렴했다. 후반부에는 A와 C가 비슷하고 B가 조금 높은 학습 loss를 보이지만, 이 값만으로 실제 로봇 성공률을 판단할 수는 없다. 다음 단계는 추가 학습이 아니라 각 모델을 낮은 속도·비상정지 준비 상태에서 소수 시험하는 것이다.

권장 1차 실제 평가:

1. 모델별로 학습 배치와 가까운 쉬운 위치 3회
2. 위치 순열 6개에서 모델별 1회씩
3. 성공/오인식/파지 실패/바구니 실패를 분리 기록
4. 실패한 조건만 추가 녹화해 기존 원본과 별도 데이터셋으로 보존

실제 로봇 추론 및 동작은 안전 확인과 사용자 승인 후에만 실행한다.

## Jetson 배포 준비

세 최종 모델은 Jetson의 `/home/USER/so101-medicine-bootstrap/models/` 아래에 복사됐고 데스크톱 원본과 SHA-256이 일치한다. LeLab에는 다음 이름으로 등록되어 각각 하나의 로컬 체크포인트가 보인다.

- `ACT-C medicine C to basket C 3cam 10k`
- `ACT-B medicine B to basket B 3cam 10k`
- `ACT-A medicine A to basket A 3cam 10k`

아직 추론 또는 로봇 동작은 실행하지 않았다.

### Jetson CUDA 차단 해결

기존 LeLab Python 3.14 환경의 범용 PyTorch는 Orin compute capability 8.7 커널을 실행하지 못하므로 그대로 보존만 했다. 대신 `/home/USER/venvs/lelab-jetson-py310`에 Python 3.10, Jetson용 PyTorch 2.8.0/torchvision 0.23.0, CUDA 12 cuSPARSELt 및 Python 3.10 호환 복사본을 설치했다.

CUDA 호환 LeLab은 `lelab-jetson-py310.service`로 실행되며 주소는 `http://127.0.0.1:8022/`이다. HTTP health, CUDA 실제 행렬연산, A/B/C 모델의 `cuda:0` 로드, 모든 텐서 유한값 및 입력 키 일치를 통과했다. 검증 명령은 `/home/USER/so101-medicine-bootstrap/tools/verify_jetson_act_models.sh`이다.

이 검증은 정책 forward/select-action, 카메라 접근, 시리얼 접근, 추론 및 로봇 동작 전에 중단했다. 실제 로봇 시험은 작업 공간 정리, 비상정지/전원 차단 준비, 대상과 같은 A/B/C 정책 선택, 사용자 명시 승인을 모두 확인한 뒤에만 진행한다.
