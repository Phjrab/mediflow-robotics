# MediFlow SO-ARM101 자율 약통 분류 프로젝트

3대의 카메라와 SO-ARM101 로봇팔을 이용해 약통 A/B/C를 인식하고 지정된 바구니로 옮기는 자율설계 프로젝트입니다. 이미지 기반 상태 판단에는 Qwen3-VL 계열 VLM을, 시연 동작 학습에는 LeRobot ACT 정책을 사용했습니다.

## 현재 상태

- 카메라 3대: 천장 수직, 천장 사선, 엔드이펙터 시점 구성
- VLM: 자세·파지 부위 판단용 이중 어댑터까지 오프라인 평가 완료
- ACT: A/B/C별 60회 시연으로 10,000 STEP 정책 학습 완료
- ACT-A v2: 60 에피소드, 32,597프레임으로 100,000 STEP 학습 완료
- ACT-A v3 trim05: 60회/27,564프레임 대기 구간 축소 데이터 검증, 100K 모델 확인·등록
- W&B: 모델별 학습 loss와 처리 속도 기록
- 실로봇 평가: ACT-C가 떨림과 접근 실패를 보여 원인 진단 및 안전 제한을 적용함
- 안전 원칙: 학습 loss가 낮더라도 실제 성공으로 간주하지 않으며, 감독 없는 로봇 동작은 허용하지 않음
- 2026-10-07 현재: 접근·파지는 관찰했지만 안정적인 바구니 투입은 미달성. 종료 토크 및 텔레옵 절대 자세 추종 수정은 배포했으며 실제 검증은 남아 있음

## 주차별 진행 기록

| 주차 | 기간 | 핵심 내용 |
|---|---|---|
| [1주차](docs/weekly/current/week01.md) | 2026-08-10 ~ 08-16 | 목표·시스템 역할（초기 설계 재구성） |
| [2주차](docs/weekly/current/week02.md) | 2026-08-17 ~ 08-23 | 라벨·출력 스키마（초기 설계 재구성） |
| [3주차](docs/weekly/current/week03.md) | 2026-08-24 ~ 08-30 | 촬영 조건·수집 규칙（초기 설계 재구성） |
| [4주차](docs/weekly/current/week04.md) | 2026-08-31 ~ 09-06 | QA·평가 기준（초기 설계 재구성） |
| [5주차](docs/weekly/current/week05.md) | 2026-09-07 ~ 09-13 | 모듈·개발 순서（초기 설계 재구성） |
| [6주차](docs/weekly/current/week06.md) | 2026-09-14 ~ 09-20 | 촬영 웹 도구·카메라 설정 구현 |
| [7주차](docs/weekly/current/week07.md) | 2026-09-21 ~ 09-27 | 데이터 검수·VLM 학습·3카메라 기반 |
| [8주차](docs/weekly/current/week08.md) | 2026-09-28 ~ 10-04 | ACT A/B/C·Jetson 배포·A-v2 100K |
| [9주차](docs/weekly/current/week09/README.md) | 2026-10-05 ~ 10-11 | ArUco·A-v3·실패 진단·종료/텔레옵 수정 |

최신 주차 기준은 [주차별 목차](docs/weekly/current/README.md)입니다. 이번 주를9주차로 맞추되 실험 날짜는 보존했습니다. 1~5주차는 실제 수행일이 확인되지 않은 기획 재구성입니다. 기존10월3일 기준 문서는 이전 정리본으로 보존합니다. 최신9주차는 다음 다섯 부분으로 분류했습니다.

1. [ArUco·OpenClaw·작업공간](docs/weekly/current/week09/01_vision_and_coordinates.md)
2. [대기 구간 축소·A-v3 학습](docs/weekly/current/week09/02_dataset_and_training.md)
3. [실로봇 시험·실패 분석](docs/weekly/current/week09/03_physical_trials.md)
4. [종료 토크·텔레옵 자세 추종](docs/weekly/current/week09/04_control_and_teleop.md)
5. [현재 상태와 다음 계획](docs/weekly/current/week09/05_status_and_next_steps.md)

## 주요 정량 결과

| 항목 | 결과 |
|---|---:|
| 초기 원본 데이터 무결성 검증 | 1,226장 / 1,226 레코드 정상 |
| 최종 VLM 이중 어댑터 테스트 | 자세 84.7%, 파지 부위 81.1%, 동시 일치 68.5% |
| ACT A/B/C 10K 최종 loss | A 0.211 / B 0.232 / C 0.209 |
| ACT-A v2 데이터 | 60 에피소드 / 32,597 프레임 / 3카메라 |
| ACT-A v2 100K 최종 loss | 0.0514 |
| ACT-A v2 학습 시간 | 12시간 00분 47초 |

## 저장소 구성

현재 GitHub 저장소와 로컬/LAN 사이트 주소는 [접속 주소 안내](docs/SITES_AND_REPOSITORIES.md)를 확인하세요. 외부에서 누구나 접속하는 공개 웹사이트는 배포하지 않았습니다.

- `so101_vlm_capture/`: 데이터 촬영·저장·검증 웹 도구
- `vlm_training/`: VLM 데이터 준비, QLoRA 학습, 평가, 안전 추론
- `lerobot_training/`: ACT 학습·평가·Jetson 배포 보조 도구
- `dataset_v2/`, `dataset_v3/`: 데이터 메타데이터, 분할, 평가 결과
- `outputs/`: 실험 보고서, 그래프, 카메라 및 오프라인 진단 결과
- `docs/weekly/`: 자율설계 주차별 진행 기록

## 대용량 파일 정책

공개 코드와 기록의 범위·예시 경로는 [공개 보관 정책](docs/PUBLIC_REPOSITORY_POLICY.md)을, 완료/미완료 상태는 [최신 상태 요약](PROJECT_STATUS.md)을 확인하세요. 이 저장소는 원본 영상·모델을 포함한 전체 현장 백업이 아닙니다.

Git 이력에는 재현 가능한 코드·설정·평가 결과·그래프를 보관합니다. 추가 요청에 따라 원본 촬영 자료와 ACT/VLM 모델은 별도 GitHub Releases 다운로드 자료로 준비합니다. 가상환경, 인증정보, 전체 중간 체크포인트와 optimizer 상태는 공개하지 않습니다. 실제 업로드 여부와 파일 목록은 [자료 업로드 안내](docs/RELEASE_ASSETS.md)를 확인하세요. 현장 원본은 유지합니다.

## W&B 학습 기록

- [ACT-A v2 100K](https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/5c90n0rr)
- [ACT-A 10K](https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/jcdrgxq2)
- [ACT-B 10K](https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/pxg9ixpi)
- [ACT-C 10K](https://wandb.ai/gyeyeongjo-chosun-university/mediflow-so101-act/runs/j4qkma86)

## 주의

이 저장소의 모델은 연구·교육용입니다. 실로봇 실행은 작업 공간 확인, 시작 자세 검증, 카메라 실시간성 확인, 물리적 전원 차단 준비와 사람의 명시적 승인 후에만 수행해야 합니다.
