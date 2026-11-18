# SO-101 무인 준비 결과

작성: 2026-11-05T01:44:57.582345+09:00 / 작업 시작: 2026-11-05T00:04:03.733946+09:00 / 현재까지 경과 약 100.9분.

**결론: 비serial 준비와 오프라인 평가는 완료했다. 첫 읽기에서 shoulder_pan의 Torque_Enable=1을 관측해 모든 후속 serial 작업을 중단했다. live 준비 완료나 G5/G6 통과로 기록하지 않는다.**

## [ACTUAL_EXECUTED]

| 위치 | 실행한 명령 또는 동작 | 결과 |
|---|---|---|
| Windows | `python -B -m unittest discover -s tests` (배포 package 폴더) | 46 통과 / 실패 0 / 오류 0 / 제외 0, exit 0 |
| Windows | AST 문법 검사, 고정 배포 경로·해시 검증, 결과 압축 해제 | 원본을 건드리지 않고 보고 폴더에 저장 |
| Windows | 원본 작업 폴더와 별도인 저장소 사본에 작업 브랜치 생성 | `vla/overnight-readonly-20261104`, push 없음 |
| Jetson | `source ~/nov_env.sh` 후 새 배포 폴더에서 문법·모의 테스트 | 기존 venv와 설치본 수정 없음. 최종 테스트 기록은 TEST_RESULTS.json |
| Jetson | `python -B nov_readonly.py` | Torque_Enable 1개 ON 관측 즉시 중단. 레지스터 쓰기 0, 포트 해제 |
| Jetson | `python -B inspect_assets.py` | 모델 해시, 원본 6개, 저장된 전처리, 기존 도구 16개, 필터링한 bash 기록 167항목 조사 |
| Jetson | `python -B capture_cameras.py` | 카메라만 열어 세 장과 두 시점 비교 저장, 모두 닫음 |
| Jetson | `python -B prepare_samples.py` | 포함된 9회차에서 시작/중간 18표본 추출, 원본 변경 없음 |
| Jetson | `python -B evaluate_offline.py` | 저장 입력으로 12회 지연 측정, 18표본×50×6 예측 비교, 언어 조건 비교 |
| Jetson | `python -B analyze_results.py` | 저장 CSV로 오차, 언어 차이, 제한 계산 재집계. 재추론 없음 |
| Jetson | `python -B verify_refusals.py` | HOME, torque --enable, dry wrapper 모두 장치 연결 전 기대 거부, 각 exit 1 |
| Jetson | `sudo -n fuser -v <장치 경로>`와 `python -B final_system_status.py` | follower·손목·RealSense 비점유, 자체 프로세스 없음, Astra active |

SSH 인증은 대화형 프롬프트로만 처리했다. 비밀번호를 파일, 명령 인자, 문서, 커밋에 넣지 않았다. 범용 원격 RPC 방식은 자동 승인 검사에서 거절되어 실행하지 않았고, 검토된 개별 SSH 명령과 경로가 고정된 배포 도구로 진행했다.

## [FILES]

Jetson 배포 경로는 `~/nov_live_prep_20261104/`다. 기존 최상위 v4, LeLab 설치 파일, venv, 체크포인트와 원본 데이터는 수정하지 않았다. 배포 폴더 안 기존 파일을 바꿀 때는 `.bak_날짜`를 먼저 만들었다.

| 파일 | 변경 내용 |
|---|---|
| nov_readonly.py | raw READ 후 `_normalize` 1회, READ 패킷 허용 목록, 쓰기 거부, 토크 ON/45°C 공유 중단, 포트 독점·해제 |
| home_align_web.py | 읽기 캐시 기반 웹, 원시 위치 정규화, 종료·예외 포트 해제, 최대 실행 시간 |
| torque_so101.py / operator_core.py | 관절별 진단과 ENABLE 전 상태 확인. 부분 ON disable은 가짜 버스 시험 전용, 실제 쓰기 CLI는 거부 |
| nov_preflight.sh / nov_policy_run.sh / dry_inference.py | 점유 조회 오류도 차단, 중단 기록 우선, 무인 dry 전용. live·send_action 경로 없음 |
| capture_cameras.py / prepare_samples.py / policy_api.py | 카메라 저장, 원본 영상·action 표본 읽기, 저장된 전처리·후처리 1회 적용 |
| evaluate_offline.py / analyze_results.py | 전체 chunk 오차·지연·언어 조건·clip 순수 계산 |
| so101_first_action_test_v5_proposal.py | v4를 수정하지 않은 별도 순수 안전 판정 제안. 하드웨어 실행기 아님 |
| tests/ | 새 코드에 대한 모의 테스트 46개 |
| RUN_ORDER.md / TOMORROW_CHECKLIST.md | ④-1 HOME 웹 종료·포트 해제, 현재 중단 기준, 현장 순서 |
| TORQUE_TOOL_AUDIT.md / LIVE_STATIC_REVIEW.md | 토크를 켜거나 남길 수 있는 경로, v4 중단·지연·HOLD 문제와 수정 제안 |
| EMERGENCY_ONE_PAGE.md / BC_RECORDING_PLAN.md | 현장 비상 절차와 B/C 수집 계획 |
| reports/BACKUPS.csv | 배포 폴더의 백업 상대 경로 목록. 종료 OS 점검 시 90개, 이후 최종 문서 배포 백업은 추가될 수 있음 |
| reports/offline_eval_v2/ | 전체 예측·실제 action CSV, 관절 오차, 언어 비교, 지연 및 상세 요약 |
| reports/offline_samples/camera_comparison.png | 행은 손목/Astra/RealSense, 열은 빨강/초록/파랑 학습 영상과 현재 영상 |

로컬 작업 커밋 정보는 GIT_RECEIPT.md를 확인한다. 원본이나 이전 결과를 삭제하지 않았다. 롤백은 현장에서 프로세스가 없는 상태를 확인한 뒤 BACKUPS.csv의 대응 `.bak_날짜` 파일을 복원하는 방식으로 검토한다. 무인 중단 기록은 롤백으로 지우지 않는다.

## [TIME]

초기 전체 예상은 240분이었다. 아래 시간은 시작·완료 기록 사이의 **경과 구간**이며 GPU 작업과 문서 작업이 겹쳐 합산하면 전체 시간과 다르다. 개별 문서 작성 시간은 사후 일괄 기록되어 정확한 배분이 미측정이다. 0.00분을 실제 작성 시간이 0이라는 뜻으로 해석하지 않는다. serial 차단은 즉시 기록하고 비serial 작업으로 전환했으며, 같은 차단 원인을 30분 넘게 재시도하지 않았다.

| 항목 | 작업 | 예상 | 기록된 경과 | 상태 |
|---|---|---:|---|---|
| P1-1 | 공통 읽기 안전 계층·home 웹 검토/수정 | 20분 | 9.7분 | DONE |
| P1-2 | 토크 거부·disable 모의 경로 | 15분 | 2.68분 | DONE |
| P1-3 | 토크 관련 기존 도구/기록 조사 | 10분 | 43.23분 | DONE |
| P1-4 | preflight 및 모니터 점유 실제 시험 | 15분 | 2.0분 | BLOCKED |
| P1-5 | Jetson 배포·문법·모의 테스트 | 10분 | 11.68분 | DONE |
| P1-6 | 실제 읽기·웹·ENABLE 전 거부 시험 | 15분 | <1분 / 개별 배분 미측정 | BLOCKED |
| P1-7 | RUN_ORDER 갱신 | 10분 | 2.0분 | DONE |
| P2-8 | 3카메라/학습 프레임 비교 | 20분 | 63.49분 | DONE |
| P2-9 | 모델 SHA256 대조 | 5분 | 8.6분 | DONE |
| P2-10 | 쓰기 차단 dry wrapper 실행 | 15분 | 3.96분 | BLOCKED |
| P2-11a | 동일 입력 10회 이상 지연/메모리 측정 | 15분 | 19.73분 | DONE |
| P2-11b | 3색 데이터셋 chunk 오프라인 오차 | 25분 | 19.73분 | DONE |
| P2-11c | 언어 조건/실시간 입력 비교·시간 계산 | 15분 | 35.14분 | DONE |
| P3-12 | live 경로 정적 검토·v5 모의 검증 | 20분 | 5.48분 | DONE |
| P3-13 | 비상 중단 한 장 | 5분 | <1분 / 개별 배분 미측정 | DONE |
| P3-14 | 내일 현장 체크리스트 | 10분 | <1분 / 개별 배분 미측정 | DONE |
| P3-15 | B/C 수집 계획 | 5분 | <1분 / 개별 배분 미측정 | DONE |
| END | 최종 읽기·정리·보고·작업브랜치 커밋 | 10분 | 7.94분 | DONE |

최종 완료 시각과 남은 작업의 상태는 함께 배포한 PROGRESS.md가 기준이다. 일부 항목의 초기 테스트 수 15/16/10은 중간 기록이며 최종 새 테스트 수는 46이다.

## [RESULTS]

### 실제 읽기와 종료

- 최초 관측: `shoulder_pan Torque_Enable=1`. 즉시 SERIAL_STOP.json 저장 후 `disconnect(disable_torque=False)`로 닫았다. ON 원인은 **UNVERIFIED**이다. 포트 열기만으로 켜졌다고 결론내리지 않았다.
- 나머지 5개 토크, 6개 모터 온도·모드·위치 및 현재 HOME 차이는 **UNVERIFIED / NOT_READ**다. 이전 저녁 값을 현재 값으로 대체하지 않았다.
- 사용자 규칙의 ON 관측 중단 조건이 최종 재읽기 요구보다 우선하므로, 종료 시 여섯 토크와 온도 재읽기도 **NOT_RUN**이다. 토크 OFF 명령도 쓰지 않았다.
- 종료 OS 확인: port_free=True, fuser exit=1, 출력 없음, 자체 작업 프로세스 0개, 8765 listening=False, Astra=active, LeLab=inactive.
- HOME 실제 갱신·모니터가 follower를 잡은 동안의 실제 preflight 시험은 BLOCKED다. PID/오류 거부 판정 모의 테스트와 중단 후 진입 거부만 확인했다.

### 모델과 카메라

- 실제 Jetson 모델 SHA256은 `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654`로 지정 Windows 값과 일치했다.
- 저장된 전처리 기준: camera1=end_effector(손목), camera2=ceiling_vertical(Astra), camera3=ceiling_oblique(RealSense). 추론 코드가 이름 변환을 중복 적용하지 않는다.
- 현재 3대 모두 640×480 RGB 프레임을 받았으나 영상은 매우 어둡다. RGB 평균은 손목 0.997, Astra 0.0036, RealSense 3.978(0~255), Astra 검정 픽셀 비율은 99.9795%였다. 학습 비교 이미지를 직접 확인했다.
- 두 프레임 해시가 다르더라도 센서 노이즈일 수 있어 유효한 장면이나 producer 신선도를 보증하지 않는다. 배치 일치와 암부 원인(조명/노출/가림)은 **UNVERIFIED**다. 브리지를 재시작하거나 노출을 바꾸지 않았다.

### 오프라인 추론

| 항목 | 결과 |
|---|---:|
| 모델·processor 로딩 | 34.902초 |
| 동일 입력 반복 | 12회 |
| 첫 회 / 전체 평균 / 첫 회 제외 평균 / 최대 | 2.939 / 2.057 / 1.977 / 2.939초 |
| PyTorch GPU peak | 1,280,764,928바이트, 약 1.193GiB |
| 프로세스 RSS / 관측 시 가용 RAM | 약 3.547GiB / 1.674GiB |
| 비교 표본 | 색별 3회차 × 시작·중간 = 18개 |
| 비교 action | 표본별 50×6 전체 |
| 평가 오류 | 0 |

Orin 공유 메모리에서 GPU peak와 RSS는 겹칠 수 있어 더하지 않는다. PyTorch peak는 CUDA context 등 모든 할당을 대표하지 않는다. 추론 후 Jetson CPU 센서는 51.5°C, GPU 센서는 50.843°C였다. 이는 읽지 못한 **모터 온도가 아니다**.

학습에 포함된 9회차 재현 오차이며 held-out 성능·로봇 성공률이 아니다. 예를 들어 팔꿈치 MAE는 시작 구간에서 색별 2.40~3.51도, 중간에서 5.24~7.10도였다. 가장 큰 몸체 오차는 빨강 중간 wrist_flex의 22.9543도다. 관절별·색별·구간별 전체 값은 EVALUATION_RESULTS.md와 aggregated_errors.csv를 확인한다.

같은 입력·seed101에서 문장만 바꾸면 action이 달라졌고 같은 문장 반복 차이는 0이었다. 빨강 중간 표본에서 red↔blue 몸체 평균 차이는 12.43255도, 최대 차이는 61.567619도였다. **언어 반응은 관측했으나 올바른 바구니 선택은 검증하지 않았다.**

30Hz에서 50목표의 명목 시간은 1.667초다. 0.5도/step×30Hz=목표 변화량 15도/초로 20도/초 설정 상한보다 먼저 제한한다. 실제 모터 속도 보장은 아니다. 첫 회 제외 평균 추론 1.977초와 합친 동기 주기는 명목 약 3.644초다. 18표본의 clip된 step은 24~50/50이었다. 이는 직전 목표를 완벽하게 따른다고 가정한 계산이며 실기 실행 시간이나 추종 성능이 아니다.

### 오류와 해결

1. 첫 배포 실행에서 cuSPARSELt 환경 경로가 빠져 import가 실패했다. 기존 환경을 고치지 않고 `source ~/nov_env.sh`로 실행 경로를 맞췄다.
2. 임시 robot 객체 수명 종료로 bus가 닫히는 문제를 발견해 ReadSession이 강한 참조를 유지하도록 수정하고 모의 회귀를 추가했다.
3. 추출한 정책 보조 함수에 `struct` import가 빠져 첫 평가가 실패했다. import를 수정하고 별도 `offline_eval_v2` 출력에서 성공했다. 실패 출력도 보존했다.
4. clip 계산의 NumPy scalar 형식 오류는 Python float 변환으로 수정했다. 저장된 예측 CSV로만 corrected 결과를 계산했고 기존 결과를 덮어쓰거나 재학습하지 않았다.
5. 토크 ON은 해결했다고 주장하지 않는다. 추가 접근을 중단하고 포트만 OS에서 확인했다. 매우 어두운 영상도 원인 미확인 상태로 남겼다.

## [NEEDS_USER]

내일 첫 명령은 저장 보고서 읽기다. 장치를 열지 않는다.

```bash
cat ~/nov_live_prep_20261104/OVERNIGHT_REPORT.md
```

이어 TOMORROW_CHECKLIST.md의 순서를 따른다. 사람이 로봇 앞에서 지지·전원·조명·가림과 정지 수단을 확인해야 한다. 새 안전 읽기에서 여섯 OFF 및 모터 온도 45°C 미만을 확인하기 전 HOME/토크 준비로 넘어가지 않는다. 현재 중단 파일을 무시하거나 삭제하는 명령은 제공하지 않았다.

v4는 동기 추론 중 감시 공백, 실패 시 Goal HOLD의 토크 재활성화 가능성, 누적 clip·producer 신선도·독립 정지·물리 한계 검증이 남아 있다. 별도 v5는 순수 판정 제안과 모의 시험만 완료했으며 실행 가능한 live 대체품은 아니다.

## [UNVERIFIED]

토크 ON의 정확한 발생 원인, 나머지 다섯 토크·모터 온도·모드·현재 위치, 현재 HOME 오차, 실제 follower 모니터 점유 시 차단, 현재 장면 품질·구도와 암부 원인, 실시간 입력 첫 action 차이, 물리 속도·부하·정지 반응·실기 성공률, RobotController 단일 경로와 G5/G6 통과.

## [NOT_RUN]

모든 레지스터 쓰기(토크 OFF 포함), robot.connect/configure/calibrate, ENABLE/LIVE 입력, live, 최종 여섯 토크 재읽기, 중단 후 serial 접근, 실시간 관절값 결합 dry 추론, DRY_REVIEW_NUMERIC_PASS, 실제 disable, 학습·재학습, 기존 venv/LeLab 수정, 시스템 설정·서비스 변경, 원본 삭제·덮어쓰기, git push. 데이터가 Jetson에 이미 있어 Windows→Jetson 원본 데이터 복사와 패키지 설치도 불필요하여 NOT_RUN이다.
