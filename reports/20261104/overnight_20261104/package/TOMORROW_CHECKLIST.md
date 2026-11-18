# 내일 현장 체크리스트

현재 차단 사유는 **최초 읽기에서 shoulder_pan 토크 ON**과 **매우 어두운 카메라 영상**이다. 나머지 모터 상태는 미확인이다. 이 문서는 현재 차단을 해제하거나 live를 실행하는 절차가 아니다.

| 순서 | 현장 작업 또는 명령 | 예상 시간 | 통과 / 중단 기준 |
|---|---|---|---|
| 1 | 팔, 지지대, 케이블, 약통과 전원 차단 수단을 눈으로 확인 | 3분 | 처짐, 접촉, 과열, 이상 소음이면 진행 중단 |
| 2 | `python3 -B -m json.tool ~/nov_live_prep_20261104/SERIAL_STOP.json` | 5초 | 저장된 관측만 확인. 전원 재투입만으로 모두 OFF라고 가정하지 않음 |
| 3 | `sudo -v && bash ~/nov_live_prep_20261104/nov_preflight.sh` | 10초 | 현재는 PORT_FREE 뒤 공유 중단 기록으로 BLOCKED가 정상. PID, 권한 오류도 중단 |
| 4 | 조명, 렌즈 가림, 학습 당시 배치를 눈으로 확인 | 3분 | Astra의 약 99.98% 검정 화면이 유지되면 진행 불가. 원인은 아직 미확인 |
| 5 | `source ~/nov_env.sh && ~/venvs/smolvla-py310/bin/python -B ~/nov_live_prep_20261104/capture_cameras.py` | 30~60초 | 약통, 바구니, 집게가 보이는 640×480 RGB 영상과 학습 비교 이미지 확인 |
| 6 | 로봇 앞에 돌아왔음을 알리고 새 읽기 작업을 지시 | 1분 | 중단 파일을 자동 삭제하지 않음. 새 안전 읽기에서 여섯 OFF와 모터 온도 45°C 미만 확인 전 다음 단계 금지 |
| 7 | RUN_ORDER의 ④-1: 현장 전원 상태 확인 → HOME 웹 → Ctrl+C → 비점유 확인 | 5~10분 | 여섯 OFF, HOME ±5도, HOME_MONITOR_CLOSED와 비점유 확인. ON인 팔을 억지로 움직이지 않음 |
| 8 | 모델, 보정, 카메라 검증 후 관측 dry-run과 첫 목표 차이 검토 | 1~3분 | 이번 무인 작업에서는 BLOCKED. 오래된 state나 가정한 값을 현재 값으로 사용하지 않음 |
| 9 | 현장 live 실행기와 독립 정지 수단 검토 | 미정 | 이 배포본은 무인 쓰기 금지 프로파일이다. 첫 동작 시험과 G5/G6 통과를 구분 |

HOME 모니터 명령은 RUN_ORDER.md에 있다. 현재 중단 기록 때문에 연결 전에 거부되는 것이 정상이다. 종료는 Ctrl+C → HOME_MONITOR_CLOSED → 포트 비점유 확인 순서다. 브라우저 탭만 닫으면 serial 프로세스가 종료되지 않는다.

live 명령은 이번 범위에 포함하지 않는다. EMERGENCY_ONE_PAGE.md와 LIVE_STATIC_REVIEW.md를 먼저 읽는다.
