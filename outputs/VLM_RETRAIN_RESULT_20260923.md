# MediFlow VLM 재학습 결과 (2026-09-23)

## 결론

검수 승인본으로 4개 필드 VLM을 재학습했고, 학습 전 기본 모델보다 성능은 개선됐다. 그러나 현재 모델은 `medicine_id=B`, `grasp_region=body` 쪽으로 예측이 치우쳐 있어 실제 로봇 분류/동작에 사용하면 안 된다.

로봇 실제 동작, calibration, ArUco, Depth 설정은 실행하거나 변경하지 않았다. 기존 원본 `data/pilot`도 수정하지 않았다.

## 사용한 데이터

- 검수 페이지 전체: 944장
- 승인: 664장
- 제외: 5장
- 아직 미검수인 예전 근접 중복 후보: 275장
- 승인본 중 학습 후보: 640장
  - `after_place` 22장 제외
  - `tilted` 2장 제외
- 같은 세션, 같은 촬영 단계, 같은 4개 라벨 안의 근접 중복: 258장 제외
- 최종 데이터셋: 382장
  - 학습 256장 / 검증 65장 / 테스트 61장
  - 학습·검증·테스트 사이 세션 중복: 0

최종 라벨 수:

| 항목 | 수량 |
|---|---:|
| 약 A / B / C | 124 / 149 / 109 |
| 세움 / 쓰러짐 | 188 / 194 |
| 몸통 / 뚜껑 / 판단 불가 | 226 / 109 / 47 |
| 잡기 전 / 잡은 후 | 281 / 101 |

## 학습 조건

- 기반 모델: Qwen3-VL-2B-Instruct
- 방식: 4-bit QLoRA
- GPU: RTX 3060 12GB
- 학습: 3 epoch, 96 optimizer steps
- 소요 시간: 약 10분 28초
- 검증 손실: 4.463 → 4.434 → 4.432
- 출력 스키마: `medicine_id`, `orientation`, `target_bin`, `grasp_region`

## 격리 테스트 결과

테스트 61장은 학습에 사용하지 않았고, 동일 촬영 세션도 학습/검증에 포함하지 않았다.

| 모델 | JSON 정상 | 4필드 전체 정답 | 약 종류 | 자세 | 목표 통 | 파지 부위 |
|---|---:|---:|---:|---:|---:|---:|
| 학습 전 기본 모델 | 55/61 | 3.3% | 19.7% | 37.7% | 19.7% | 42.6% |
| 새 모델 1 epoch | 61/61 | 4.9% | 37.7% | 37.7% | 37.7% | 63.9% |
| 새 모델 3 epoch | 61/61 | **8.2%** | 26.2% | **59.0%** | 26.2% | **63.9%** |

3 epoch 모델은 테스트 61장 모두에서 약 종류를 B, 파지 부위를 body로 출력했다. 따라서 8.2%라는 개선 수치만 보고 성공으로 판단할 수 없다. 현재 결과는 다수 라벨을 고르는 방식에 가까우며 일반화가 되지 않았다.

## 왜 아직 부족한가

1. 근접 연속 사진을 제거하면 독립적인 장면이 382장뿐이다. 사진 수보다 **서로 다른 배치·각도·거리·조명·배경을 가진 세션 수**가 중요하다.
2. 파지 부위는 body 226장, lid 109장, unknown 47장으로 불균형하다.
3. `after_grasp`는 101장뿐이며, 테스트에서는 이 구간의 약 종류 정확도가 0%였다.
4. 카메라별 외형 차이가 크다. 3 epoch 약 종류 정확도는 Astra 22.2%, RealSense 40.0%, video4 0%, video6 38.5%였다.
5. `before_grasp` 프레임에서 그리퍼가 아직 충분히 접근하지 않았다면 lid/body 정답이 이미지에서 보이지 않을 수 있다. 이런 프레임은 라벨을 강제로 주기보다 `unknown`으로 두거나, 실제 파지가 보이는 프레임과 분리해야 한다.

## 다음 촬영 권장안

실제 추론에 사용할 카메라를 먼저 1개(필요하면 2개)로 고정한다. 카메라마다 아래 조합을 각각 **서로 다른 세션 20장 이상** 모으는 것을 권장한다.

- 약 A/B/C를 같은 수로 촬영
- upright/fallen을 같은 수로 촬영
- lid/body를 같은 수로 촬영
- before_grasp/after_grasp를 같은 수로 촬영
- 연사처럼 거의 같은 사진 대신 약통 위치, 회전, 거리, 조명, 배경, 그리퍼 접근 방향을 바꾸기
- `unknown`은 가림, 화면 밖, 파지 부위가 실제로 안 보이는 사례만 별도 수집

한 카메라 기준 최소 목표는 `3종 × 2자세 × 2파지 × 2단계 × 20세션 = 480장`이다. 기존 데이터 중 조건을 충족하는 독립 장면은 재사용하고, 부족한 조합만 추가하면 된다.

## 생성 파일

- 데이터 QA: `dataset_v3/qa_report.json`
- 학습/검증/테스트: `dataset_v3/train.jsonl`, `validation.jsonl`, `test.jsonl`
- 최종 어댑터: `work/qwen3-vl-2b-mediflow-v3-lora/final_adapter`
- 3 epoch 평가: `dataset_v3/evaluation-v3-lora.json`
- 기본 모델 평가: `dataset_v3/evaluation-v3-zero-shot.json`
- 1 epoch 평가: `dataset_v3/evaluation-v3-epoch1.json`

## 재현 명령

```bash
cd /home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2

work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.prepare_reviewed \
  --project-root . \
  --config vlm_training/config/vlm_v3.yaml

work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.train_qlora \
  --project-root . \
  --config vlm_training/config/vlm_v3.yaml \
  --train-manifest dataset_v3/train.jsonl \
  --validation-manifest dataset_v3/validation.jsonl \
  --output work/qwen3-vl-2b-mediflow-v3-lora \
  --epochs 3 --learning-rate 2e-4 --gradient-accumulation 8
```
