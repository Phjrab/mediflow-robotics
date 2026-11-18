# 이번 로컬 준비 작업 실행 기록

추가 승인 후 정책 dry-run 실제 수행: SSH에서 파란 바구니 task로 v4 `--dry-run`1회를 실행해 exit0을 확인했다. 1chunk(50×6), 정책step0, 추론3.030253초, 최고35°C. 첫목표6개모두차이1단위이내. CSV와 시작 이미지3장을 새 로컬 검토 폴더로 복사하고 직접 검수했다. camera read failed는 사선 연결 초기에3회 있었으나 이후 관측·추론·검사가 완료됐다. 새 카메라 열기·관절 읽기·추론은 이 승인 범위에서 ACTUAL_EXECUTED로 정정하며, live·정책 모터 목표 전송·학습·push는 NOT_RUN이다. 상세는 `../dryrun_result_20261104_234610/DRY_RUN_RESULT.md`이다.

최신 후속 결과: 아래 초기 NOT_RUN 기록 이후 사용자가 SSH 직접 실행을 승인했다. SSH 코드 저장·검사·serial 읽기·OFF/현재위치목표 진단·토크ON을 직접 수행했다. 현재 목표 쓰기 직후 재활성화 구간을 재현했고, 목표 쓰기를 제거한 torque_so101_verified_goal_v3.py가 TORQUE_ON_HOLD(exit0)로 종료했다. 토크6개ON/모드0/최고35°C/전후 읽힌 위치 동일, 목표쓰기0/명시적ON1. 관련 mock109개 통과(exit0). 상세·미실행·롤백은 TORQUE_PREP_FIXED.md와 TORQUE_FOLLOWUP_20261104.md를 따른다. 정책 추론·카메라 열기·학습·live·push는 이 후속 작업에서 NOT_RUN이다. 이하 내용은 초기 준비 시점 기록이다.

기준: 사용자가 이번 요청에서 확정한 2026-11-02~03 Jetson LeLab, follower id so-101, use_degrees=True, 보정 해시 및 미변경 운영자 확인. 이 정보를 units_so101.json의 evidence에 진술 출처로 기록했다. 과거 저널이 비어 있었다는 사실을 긍정 증거로 사용하지 않았다.

읽은 파일: reports/so101_first_action_test.py의 argparse/verify_units/home_gate/read_health/버스 연결 및 종료 처리. 공식 lerobot-0.6.0-py3-none-any.whl 안 motors_bus.py의 sync_read/sync_write/disconnect, feetech.py의 enable_torque 소스. API 확인은 파일읽기만 수행.

실행 명령과 결과:
- `python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_nov_prep.py' -v`: 17 통과, 실패0, 오류0, 제외0, exit0. 합성 시작프레임/임시파일/가짜 모터 버스만 사용. 원본150-제외13=137, 통계, 부분저장거부, 덮어쓰기거부, 현재목표쓰기→읽기대조→토크순서, 온도/모드/home/통신오류 무쓰기, 목표읽기실패 및 위치변화시 토크비활성화를 검사.
- `ast.parse(..., feature_version=(3,10))`: 새 Python파일 문법통과. units JSON 파싱통과. 실제Python3.10 런타임실행이 아닌 문법검사임.
- `C:/Program Files/Git/bin/bash.exe --noprofile --norc -n <파일>`: PASTE_TOOLS.sh, PASTE_UNITS.sh, nov_preflight.sh, nov_policy_run.sh, nov_env.sh 모두 exit0. 실제셸실행/서비스조회/장치열기는 하지 않음.
- 표준라이브러리 생성기로 PASTE_TOOLS.sh/PASTE_UNITS.sh heredoc 내용 작성. 이 파일들은 사용자 Jetson 붙여넣기용이며 여기서는 실행하지 않음.

추가 파일: 이 폴더의 Python3개 도구/검사도구1개/테스트, 셸3개, units JSON, 붙여넣기셸2개, RUN_ORDER.md 및 이 기록. 기존 v4/LeLab/RobotController/데이터/모델 파일은 수정하지 않았다.

NOT_RUN: Jetson/SSH, 파일전송/push, 실제137회 home 통계계산/실제home JSON생성, 실제토크ON/OFF·관절·serial·카메라열기, GPU모델로딩/추론/학습, dry-run/live. 실제home의5도포함률/실제토크무튐/현장안전은 UNVERIFIED. 단위/보정 연결은 운영자 확인에 근거하며 이번 작업이 과거자료로 독립증명했다고 표현하지 않음.

다음 작업: PASTE_TOOLS.sh 전체를 운영자가 Jetson에 붙여넣고 RUN_ORDER.md의1단계 home 통계를 실행한다. 137회가 아니면 파일생성을 거부하며 여기서 중단한다. Windows부분4개 계산은 참고용이며 전체home을대체하지 못한다.
