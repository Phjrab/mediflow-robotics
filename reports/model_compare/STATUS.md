# 모델별 학습 상태

2026-11-22 KST. 실제 기록과 평가값은 [비교 보고서](COMPARE.md)에 있다.

| 정책 | 진행 스텝 | 상태 | 학습 종료 또는 해제 조건 |
|---|---:|---|---|
| 영상·상태 MLP | 4,000/4,000 | 완료 | 평가 21회, Jetson 추론 400회 |
| 영상·상태 + 지시 MLP | 4,000/4,000 | 완료 | 평가 21회, Jetson 추론 400회 |
| ACT | 정식 0/100,000 | CUDA 미할당 | 별도 CPU 1스텝 실행. 배치 1, 임의 초기화로 자원만 점검 |
| ACT + 지시 | 0/100,000 | CUDA 미할당 | 9차원 입력과 116회 학습 분리본 준비 |
| Diffusion | 0/100,000 | CUDA 미할당 | 학습 GPU에서 배치·메모리 측정 |
| SmolVLA | 0/20,000 | CUDA 미할당 | 공개 base 고정, 학습 GPU 필요 |
| π0.5 · X-VLA | 각 0/20,000 | CUDA 미할당 | 대형 모델용 GPU와 각 모델의 의존성 필요 |
| GR00T N1.5 | 0 | 현재 LeRobot 버전 미지원 | 별도 호환 환경·학습 GPU 필요 |

GPU 큐에서 아래 RuntimeError는 모두 CUDA unavailable이다. 미시작 작업의 ETA는 산정할 수 없다. 로컬 CPU 점검 중 기존 캐시의 PyArrow dataset DLL 로딩이 실패해, 호환되는 로컬 PyAV와 Parquet 파일 리더로 같은 회차의 실제 3개 프레임을 읽어 ACT 갱신을 확인했다.

### 실행 이력

- 2026-11-22T16:19:09+09:00 · rgb_mlp · RUNNING · 0/4000 · ETA —

- 2026-11-22T16:19:12+09:00 · rgb_mlp · CHECKPOINT · 1000/4000 · ETA 2026-11-22T16:19:23+09:00

- 2026-11-22T16:19:16+09:00 · rgb_mlp · CHECKPOINT · 2000/4000 · ETA 2026-11-22T16:19:23+09:00

- 2026-11-22T16:19:19+09:00 · rgb_mlp · CHECKPOINT · 3000/4000 · ETA 2026-11-22T16:19:23+09:00

- 2026-11-22T16:19:23+09:00 · rgb_mlp · CHECKPOINT · 4000/4000 · ETA —

- 2026-11-22T16:19:23+09:00 · rgb_mlp · COMPLETE · 4000/4000 · ETA —

- 2026-11-22T16:19:24+09:00 · rgb_mlp_task · RUNNING · 0/4000 · ETA —

- 2026-11-22T16:19:27+09:00 · rgb_mlp_task · CHECKPOINT · 1000/4000 · ETA 2026-11-22T16:19:38+09:00

- 2026-11-22T16:19:31+09:00 · rgb_mlp_task · CHECKPOINT · 2000/4000 · ETA 2026-11-22T16:19:38+09:00

- 2026-11-22T16:19:34+09:00 · rgb_mlp_task · CHECKPOINT · 3000/4000 · ETA 2026-11-22T16:19:38+09:00

- 2026-11-22T16:19:38+09:00 · rgb_mlp_task · CHECKPOINT · 4000/4000 · ETA —

- 2026-11-22T16:19:38+09:00 · rgb_mlp_task · COMPLETE · 4000/4000 · ETA —

- 2026-11-22T16:19:42+09:00 · act · BLOCKED RuntimeError · step — · ETA —

- 2026-11-22T16:19:42+09:00 · act_task · BLOCKED RuntimeError · step — · ETA —

- 2026-11-22T16:19:42+09:00 · diffusion · BLOCKED RuntimeError · step — · ETA —

- 2026-11-22T16:19:42+09:00 · smolvla · BLOCKED RuntimeError · step — · ETA —

- 2026-11-22T16:19:42+09:00 · pi05 · BLOCKED RuntimeError · step — · ETA —

- 2026-11-22T16:19:42+09:00 · xvla · BLOCKED RuntimeError · step — · ETA —
