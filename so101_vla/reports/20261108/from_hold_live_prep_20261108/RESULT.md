# 토크 유지 후 첫 VLA 동작 준비 — 2026-11-08

운영자의 토크 유지 시험 성공 상태에서 시작하는 경로를 만들고 Jetson에 배포했다. 관련 모의 테스트는 노트북·Jetson 각각155개 통과했다. 실제 VLA 모터 동작은 아직 실행하지 않았다.

## 실제 수행

- SSH로 파일만 읽고 코드를 배포했다. 모델·데이터·LeLab·SDK·기존 RobotController는 수정하지 않았다.
- `hold_continuation.py`는 토크 유지 원본 로그·검토 기록·기존 FIELD_STOP·보정·home의 해시와 내용을 대조한다. 과거 토크 유지 성공 기록만으로 live 권한을 주지 않는다.
- `first_trial_from_hold.py`와 `run_first_from_hold.sh`를 추가했다. 기본값은 장치·추론 없는 PLAN_ONLY이고 `--check-files`도 파일만 검사한다.
- 실제 실행은 모델 로드/예열을 포트가 닫힌 상태에서 수행한 뒤 새 현장 확인을 받는다. 현재 상태를 다시 검사하고 기존 RobotController를 통해서만 목표를 보낸다. 토크 쓰기 경로는 없다.
- 성공 증거는 한 번만 소비하며, 이후 장치 단계에서 중단하면 새 `POST_HOLD_STOP.json`을 보존하고 추가 실행을 막는다. 이전 `FIELD_STOP.json`은 삭제하거나 변경하지 않는다.
- 관절5개 스텝당0.5도·초당20도, 그리퍼 스텝당2포인트, 30Hz, 최대10스텝/5초, 시작50도 미만·55도 중단, home/모델/단위/보정/과부하/카메라/NaN/추론 지연 검사는 유지한다. 추론 허용시간50/30초는 완화하지 않았다.

### 변경 파일10개

기존3개: `trial_runtime.py`, `arm_source.py`, `operator_arm.py`.

신규7개: `hold_continuation.py`, `first_trial_from_hold.py`, `run_first_from_hold.sh`, `test_hold_continuation.py`, `test_first_trial_from_hold.py`, `test_device_continuation_guard.py`, `test_goal_sdk_integration.py`.

Jetson 위치는 `~/v6_process_1793887709179/operator_candidate/`이다. 기존3개 백업은 `~/v6_process_1793887709179/software_backups_20261108/1794122872715404094/`에 있다. 테스트 파일의 후속 보완 전 사본은 같은 후보 폴더의 `test_first_trial_from_hold.py.bak_test_home_1794123186363414058`이다.

## 검증 결과

| 위치 | 명령 | 결과 | 종료코드 |
|---|---|---|---:|
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105/operator_candidate -p 'test_*.py'` | 140통과, 실패/오류/제외0 | 0 |
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105 -p 'test_*.py'` | 15통과, 실패/오류/제외0 | 0 |
| Jetson의 v6 작업 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s operator_candidate -p 'test_*.py'` | 140통과, 실패/오류/제외0 | 0 |
| Jetson의 v6 작업 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s . -p 'test_*.py'` | 15통과, 실패/오류/제외0 | 0 |
| Jetson | `bash -n operator_candidate/run_first_from_hold.sh` | 문법 통과 | 0 |
| Jetson | `bash operator_candidate/run_first_from_hold.sh` | PLAN_ONLY, 장치/추론0 | 0 |
| Jetson | `bash operator_candidate/run_first_from_hold.sh --check-files` | HOLD_FILES_READY, 기록 미소비 | 0 |

관련 테스트만 실행했다. 최초 Jetson 시험에서 통합 모의 테스트1개가 가짜 HOME 때문에 기존 의존 폴더를 찾지 못했다. 테스트에 의존 경로를 명시한 뒤 최종140+15 모두 통과했다. 실행 코드의 안전 조건을 바꾼 수정이 아니다.

모의 시험에는 정상10스텝의 목표 전송, 토크 쓰기0, LIVE 미확인/과열/카메라 오류 시 거부, 중단 기록 보존, 실제 SDK 함수와 가짜 포트를 사용한 목표 패킷 및 재전송 거부가 포함된다. 모의 통과는 실제 동작 성공을 뜻하지 않는다.

`FINAL_SOFTWARE_VERIFICATION.json`의10개 코드 해시는 최종 배포 목록과 일치한다. 기존 중단 해시는 계속 `e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c`이다. 단발 사용 기록은 아직 없고 새 POST_HOLD_STOP도 없다.

최초 적용 기록은 `APPLIED.json`, 테스트 파일 보완은 `TEST_ENV_PATCH.json`, 최종 상태는 `FINAL_SOFTWARE_VERIFICATION.json`으로 나눠 보존했다. 최종 패키지는 기준 커밋 `b5ef9a4`에 한 번만 적용하는 용도이며 이미 배포된 Jetson에 다시 적용하지 않는다.

최종 코드 ZIP SHA256: `d2112a99b094d6c39614642e28b3ed87c792056e88d3ce9ee26147eeab8cbafc`.

## 운영자가 다음에 직접 실행할 명령

팔이 안정적으로 지지되어 있고 움직임을 지켜보며 DC 전원을 즉시 끌 수 있을 때만 Jetson 터미널에서 실행한다. 이 명령은 확인 입력 후 실제로 움직일 수 있다. 별도 토크ON/OFF 명령은 다시 실행하지 않는다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_first_from_hold.sh --live --execute --operator-present --supported --stop-ready
```

1. Astra 서비스가 시작되고 모델 로드·예열이 끝날 때까지 기다린다. 이 구간은 모터 명령이 없으며, 최대5초 제한은 전체 명령 소요시간을 뜻하지 않는다.
2. 새 현장 검토 문구를 읽고 직접 `REARM_HOLD`를 입력한다. 이때 성공 기록이 단발 소비된다. 현재 상태가 예상과 다르면 쓰지 않고 HOLD한다.
3. 카메라 시작 이미지와 상태 검사 후 직접 `LIVE`를 입력한다. 그 뒤에만 최대10스텝/5초의 첫 동작을 시도한다.
4. `FIRST_TRIAL_COMPLETE`여도 약통 투입 성공/G5/G6가 아니다. `HOLD` 또는 예외가 나오면 그대로 재실행하지 말고 결과를 전달한다. 성공 기록과 중단 파일을 삭제하지 않는다.

Ctrl+C는 새 명령을 중단하고 포트를 닫지만 토크OFF를 보내지 않는다. 이미 전달된 목표에서 즉시 물리 정지를 보장하는 기능은 아니다. 예상 밖 움직임은 DC 전원을 차단하고 팔이 처지지 않게 받친다.

## 미확인·미실행·차단

- 미확인: 이번 소프트웨어 작업 시점의 실제 토크/온도/위치, 실제 카메라 영상과 노출, 실시간 추론 지연, 첫 동작 결과. 마지막 토크 유지 성공 기록은 당시 관측이다.
- NOT_RUN: 이번 작업에서 로봇/serial/카메라 열기, 실제 모델 추론, 토크/목표 쓰기, live, 학습, 서비스 조작.
- NOT_PUSHED: 로컬 작업 브랜치에만 커밋한다.
- 과거 목표 초기화 뒤 토크 불일치의 직접 원인은 당시 원시 응답 부족으로 여전히 미확인이다. 그 초기화 도구는 재사용하지 않으며 성공한 별도 토크 유지 기록과 새 현장 검사를 사용한다.
- 원래 FIELD_STOP과 단발 소비 검사는 유지한다. 현장 확인 입력은 운영자가 직접 해야 한다.

## 롤백

자동 롤백이나 서비스 조작은 하지 않는다. 새 실행기를 사용하지 않으면 모터 동작은 없다. 코드를 되돌릴 때는 프로세스가 없는 상태에서 위 백업의 기존3개만 복원하고 신규 파일은 사용 중지한다. 코드 롤백은 현재 토크를 끄거나 팔을 안전 자세로 옮기지 않는다. 원래 FIELD_STOP·hold_usage·POST_HOLD_STOP·현장 결과는 롤백 대상에서 제외하고 보존한다.
