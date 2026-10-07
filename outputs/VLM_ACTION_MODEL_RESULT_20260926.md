# MediFlow 2필드 VLM 학습 결과 (2026-09-26)

## 결론

약 종류와 목표 바구니를 작업 명령에서 결정하고, VLM은 이미지에서 자세와 파지 부위만
판단하도록 2필드 모델을 새로 학습했다. 1 epoch와 2 epochs를 검증해 1 epoch를 선택하고
격리 테스트 111장에 한 번 평가했다.

선택 모델은 자세 84.7%, 파지 부위 73.0%, 두 필드 동시 일치 62.2%를 기록했다.
`body`와 `lid` 재현율은 각각 86.8%, 85.7%지만 `unknown` 재현율은 18.2%다.
따라서 인식이 불확실한 장면을 자동으로 로봇 동작에 연결하면 안 되며, 승인 단계나 별도
안전 임계값이 필요하다.

로봇 제어, calibration, ArUco, Depth, 카메라 설정과 원본 데이터는 변경하지 않았다.

## 모델 구조

- 명령 담당: `medicine_id`
- 코드 담당: `target_bin = BIN_MAP[medicine_id]`
- VLM 담당: `orientation`, `grasp_region`
- 학습 응답: `orientation|grasp_region`
- 학습 데이터: 375장, 한 epoch 크기 유지
- 균형 샘플링: orientation × grasp_region 6조합을 각 62~63장
- 손실: assistant 정답 6토큰에만 적용
- 방식: Qwen3-VL-2B-Instruct, NF4 4-bit QLoRA

명령과 VLM 출력을 합치는 `compose_command_action()`을 추가했다. 예를 들어 명령의 약이 B이고
VLM이 `fallen|body`를 반환하면 최종 결과는 다음과 같다.

```json
{
  "medicine_id": "B",
  "orientation": "fallen",
  "target_bin": "BIN_B",
  "grasp_region": "body"
}
```

## 체크포인트 선택

| 검증 지표 | 1 epoch | 2 epochs |
|---|---:|---:|
| 두 필드 동시 일치 | **48.5%** | 42.4% |
| 자세 정확도 | **93.9%** | 88.9% |
| 파지 부위 정확도 | **52.5%** | 51.5% |
| 유효 출력 | 100% | 100% |

2 epochs에서 검증 성능이 감소했으므로 추가 학습을 중단하고 1 epoch를 선택했다.

선택된 어댑터:

`work/qwen3-vl-2b-mediflow-action-v1-20260926/checkpoint-47`

## 격리 테스트 결과

| 지표 | 결과 |
|---|---:|
| 유효 출력 | 111/111 (100%) |
| 두 필드 동시 일치 | 69/111 (62.2%) |
| 자세 정확도 | 94/111 (84.7%) |
| 파지 부위 정확도 | 81/111 (73.0%) |

클래스별 재현율:

- 자세: fallen 93.8%, upright 71.7%
- 파지 부위: body 86.8%, lid 85.7%, unknown 18.2%

## 다음 안전 조건

- VLM이 `unknown`을 안정적으로 구분할 때까지 실제 로봇 자동 실행에 연결하지 않는다.
- 명령에 없는 약 종류를 이미지 모델이 임의로 결정하지 못하게 한다.
- `medicine_id`와 `target_bin` 매핑이 어긋나면 실행을 거부한다.
- 파지 부위가 불확실하거나 목표가 보이지 않으면 사람 승인을 요청한다.

## 결과 파일

- 1 epoch 검증 평가: `dataset_v3/evaluation-action-v1-epoch1-validation.json`
- 2 epochs 검증 평가: `dataset_v3/evaluation-action-v1-epoch2-validation.json`
- 격리 테스트 예측: `dataset_v3/predictions-action-v1-epoch1-test.jsonl`
- 격리 테스트 평가: `dataset_v3/evaluation-action-v1-epoch1-test.json`

실제 로봇 배포와 동작은 수행하지 않았다.
