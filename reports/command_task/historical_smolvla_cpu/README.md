# 과거 배포 SmolVLA의 동결 관측 CPU 재생

실제 배포 가중치 `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654`를 재생했다. 이 가중치의 3색 학습에는 평가 21회가 이미 포함되어 있다. 이번 결과의 환경은 Windows CPU이며, 체크포인트에 저장된 CUDA AMP는 적용되지 않았다. 추론 코드는 50개 출력과 10회 디노이징을 유지했다.

시연 닫힘 다음의 동일 프레임에 빨강·초록·파랑 명령을 각각 입력했다. 21개 관측과 63회 반복 측정이다. [관측 대조](canonical_anchor_comparison.json)에서 Chosun 새 모델 재생과 회차 ID, 기록색, 프레임, 관절 상태, 세 카메라 RGB 해시 및 초기 노이즈 해시가 모두 일치했다. 두 실행은 장치와 정밀도가 다르며 학습 데이터 이력도 달라, 숫자 차이를 학습 효과로만 해석할 수 없다.

| 조건 | 50번째 행동 | 마지막 10개 평균 | 상대 이동 | 놓기 직전 기준 |
|---|---:|---:|---:|---:|
| 전체 명령 | 52/63 | 45/63 | 47/63 | 46/63 |
| 기록색 명령 | 19/21 | 18/21 | 20/21 | 19/21 |
| 다른 색으로 변경 | 33/42 | 27/42 | 27/42 | 27/42 |
| 파랑 명령 전체 | 17/21 | 15/21 | 16/21 | 18/21 |
| 파랑 기록의 파랑 명령 | 6/7 | 6/7 | 7/7 | 7/7 |
| 비파랑 기록의 파랑 명령 | 11/14 | 9/14 | 9/14 | 11/14 |

50번째 행동의 팔 5축은 학습 기준의 같은 상대 시점 색별 중심과 비교했다. 해당 판정은 시연 자체 20/21, 학습 회차를 한 번에 하나씩 제외한 계산에서 95/108이었다. 전체 명령 63회 중 거리 마진이 10% 이하인 경우는 8회였다. 비파랑 기록의 파랑 명령에서는 빨강 6/7, 초록 5/7이 파랑으로 분류됐다. 시연 중 50개 창 안에 놓기가 들어간 회차는 0/21이다. 이 수치는 닫힘 직후의 관절 방향 분류 결과이며 실제 바구니 도착은 입회 시험에서 확인해야 한다.

## 재현

기존 배포 체크포인트, 동결 표준 분할, 캐시된 SmolVLM2 토크나이저와 CPU LeRobot 환경을 사용한다. Chosun 평가에 사용한 원본 프레임을 다시 보내지 않고 Windows에 이미 있는 동결 분할에서 읽었다.

```text
python lerobot_training/model_compare/command_task.py infer DATASET_STANDARD RUNS OUTPUT --device cpu --cpu-threads 8 --checkpoint DEPLOYED_CHECKPOINT
python lerobot_training/model_compare/command_task.py score DATASET_STANDARD OUTPUT/observation.json OUTPUT/predictions.npz OUTPUT/direction.json
python lerobot_training/model_compare/command_task.py compare-anchors reports/command_task/smolvla/observation.json OUTPUT/observation.json OUTPUT/canonical_anchor_comparison.json
```

- [과거 가중치 및 입력 해시](observation.json)
- [원시 종단 좌표·거리 마진·혼동 행렬](direction.json)
- 재현을 실행하면 원시 63회분 예측을 로컬 `OUTPUT/predictions.npz`에 저장한다.
- [공통 추론·평가 코드](../../../lerobot_training/model_compare/command_task.py)
