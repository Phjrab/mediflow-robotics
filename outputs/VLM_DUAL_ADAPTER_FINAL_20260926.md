# MediFlow 이중 어댑터 최종 결과 (2026-09-26)

## 결론

추가 학습은 여기서 멈추는 것이 효율적이다. 파지 부위만 학습하는 전용 1-epoch
어댑터가 기존 2필드 모델보다 개선됐고, 더 긴 학습은 기존 실험에서 검증 성능을
떨어뜨렸다. 현재 구조는 작업 명령이 약 종류를 정하고, 코드가 목표 바구니를 정하며,
VLM이 자세와 파지 부위를 판단한다.

최종 오프라인 테스트 111장에서 자세 84.7%, 파지 부위 81.1%, 두 값 동시 일치
68.5%를 기록했다. 기존 단일 어댑터의 동시 일치 62.2%, 파지 73.0%보다 개선됐다.
그러나 완전 자율 로봇 동작에 연결할 정확도는 아니다. 현재 추론 도구는 어떤
경우에도 로봇 동작을 허가하지 않고 사람 검토만 요청한다.

## 선택 모델

- 자세: `work/qwen3-vl-2b-mediflow-action-v1-20260926/checkpoint-47`
- 파지 부위: `work/qwen3-vl-2b-mediflow-grasp-v1-20260926/checkpoint-47`
- 기반 모델: Qwen3-VL-2B-Instruct, NF4 4-bit QLoRA
- 약 종류: 작업 명령의 `medicine_id`
- 목표 바구니: 코드의 `BIN_MAP[medicine_id]`

두 어댑터는 같은 기반 모델에 함께 로드하고 순서대로 전환한다. 기반 모델을 두 번
메모리에 올리지 않는다.

## 성능 비교

| 격리 테스트 111장 | 기존 2필드 | 최종 이중 어댑터 |
|---|---:|---:|
| 자세 정확도 | 84.7% | 84.7% |
| 파지 부위 정확도 | 73.0% | **81.1%** |
| 두 값 동시 일치 | 62.2% | **68.5%** |
| body 재현율 | 86.8% | 83.8% |
| lid 재현율 | 85.7% | 85.7% |
| unknown 재현율 | 18.2% | **68.2%** |
| 출력 형식 유효율 | 100% | 100% |

검증 세트에서도 파지 정확도는 52.5%에서 57.6%, 파지 균형 정확도는 40.3%에서
50.2%, unknown 재현율은 15.8%에서 47.4%로 상승했다. 검증과 테스트가 모두 같은
방향으로 좋아져 파지 전용 어댑터를 채택했다.

## 카메라와 단계별 제한

최종 테스트의 파지 정확도는 `/dev/video6` 89.4%, Astra 69.2%, RealSense 33.3%였다.
`/dev/video4`는 표본이 1장뿐이라 판단 근거가 없다. 따라서 현재 안전 정책은 다음과
같다.

- `/dev/video6` + `before_grasp`: 사람 검토 대상으로만 전달
- Astra, RealSense, `/dev/video4`, 알 수 없는 카메라: 차단
- `after_grasp`: 관찰용으로만 취급하고 새 파지 계획 차단
- 자세 또는 파지 결과가 `unknown`: 차단
- 모든 경우: `robot_motion_allowed: false`

전체 테스트 안전 감사 결과는 111장 중 58장 차단, 53장 사람 검토 대상, 자동 동작
허가 0장이었다.

## 실행 명령

로봇과 연결되지 않은 단일 이미지 안전 추론:

```bash
work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.safe_infer \
  --project-root . \
  --config vlm_training/config/vlm_v3.yaml \
  --image PATH_TO_IMAGE \
  --medicine-id B \
  --camera /dev/video6 \
  --capture-phase before_grasp \
  --load-in-4bit \
  --output work/safe-infer-result.json
```

이 명령은 추론 결과 JSON만 저장한다. 카메라 서비스 시작, calibration 변경, 로봇
제어 및 실제 동작은 수행하지 않는다.

## 결과 파일

- 파지 전용 검증: `dataset_v3/metrics-grasp-v1-validation.json`
- 파지 전용 격리 테스트: `dataset_v3/metrics-grasp-v1-test.json`
- 결합 검증: `dataset_v3/evaluation-dual-v1-validation.json`
- 결합 격리 테스트: `dataset_v3/evaluation-dual-v1-test.json`
- 카메라/단계별 분석: `dataset_v3/dual-v1-test-slices.json`
- 안전 감사: `dataset_v3/dual-v1-safety-audit.json`
- 사람 검토 큐: `dataset_v3/dual-v1-safety-queue.jsonl`
- 단일 이미지 실동작 점검: `work/safe-infer-dual-smoke-scene_000145.json`

## 사람이 해야 하는 다음 작업

현재 자동화 가능한 학습·평가·안전 검사는 완료됐다. 다음 단계에서 실제 사람이 해야
하는 일은 두 가지다.

1. 성능을 더 높이려면 `/dev/video6`, `before_grasp` 조건으로 헷갈리는 장면을 추가
   촬영하고 라벨을 검수한다. 특히 똑바로 선 약통의 자세와 몸통/뚜껑 경계가 가려진
   장면이 우선이다.
2. 실제 로봇 연결 전에는 안전 구역에서 사람이 각 추론 결과를 확인하고 별도의 수동
   승인 절차를 설계해야 한다.

새 데이터 없이 같은 데이터를 더 오래 학습하는 것은 현재 근거상 비효율적이다.
