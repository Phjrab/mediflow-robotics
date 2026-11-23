# 모델별 학습 상태

### 2026-11-23 · RTX 3060 및 파랑 7회 우선 평가

- 승인된 작업은 약통 A의 파란 바구니 투입이다. seed 101의 기존 3색 학습 116회(53,709프레임), 평가 21회(9,842프레임)를 유지한다. 파랑 평가 회차는 97, 98, 103, 105, 109, 121, 136이고 3,163프레임이다.
- 연결된 학습 PC 터미널에서 NVIDIA GeForce RTX 3060, VRAM 총 12,288 MiB·당시 여유 2,878 MiB, GPU 사용률 57%, 디스크 여유 134 GiB를 실측했다. PyTorch 2.11.0+cu130, CUDA 13.0에서 장치 텐서 생성과 값 회수가 통과했다. 수치는 첫 연결 시점의 장치 단위 측정값이다.
- 이후 원격 채팅 생성에서 요청한 모델 ID 검증 실패, 로컬 담당 채팅의 원격 인계에서 작성창 상태 오류, SSH에서는 학습 PC 이름 해석 실패를 확인했다. 추가 PID별 측정·함께 실행 중인 작업의 로그/체크포인트 갱신·확보 VRAM 재측정은 미확인이다. 종료한 프로세스 0건. 새로운 GPU 학습과 자원 시험은 아직 미실행이다.
- 모델 순서는 SmolVLA(20,000스텝·배치 4), 지시 ACT, 기본 ACT, Diffusion, X-VLA, 자원이 허용하는 경우 π0.5다. 각 모델의 1~2스텝 실측과 최대 8시간 조건이 적용된다. 스텝과 자원은 실제 기록이 생성된 뒤 기입한다.
- 보존 프레임 재판독으로 기존 배포 모델의 투입 3건을 확인했다. 가운데 실행에는 사람의 재배치가 있었다. [프레임 근거](EVIDENCE_AUDIT.md)
- Jetson 저장 영상 평가 완료. 3대 RGB 전처리 포함 MLP p95는 기본 7.11 ms·지시 6.34 ms, 기존 배포 SmolVLA의 교체 지시 일치는 11/42다. 학습 회차 노출을 명시해 기존 모델은 참고 항목에만 둔다. [지연 원자료](jetson_rgb_latency.json) · [교체 시험](historical_smolvla_prompt_check.json)

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
