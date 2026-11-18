# 승인된 시작 오차 허용폭 적용·재개 준비 — 2026-11-08

사용자가 앞서 제안한 시작 검사 변경을 승인하고 이전 완화 금지 지시를 취소했다. 시작 위치/목표 대조에 원시값1칸 이하와 정규화 차이0.1 이하를 함께 적용했다. 관절5개는 degree, 그리퍼는0~100 단위이므로 그리퍼 허용폭은0.1포인트다. 이동량·온도·HOLD 등 나머지 조건은 유지했다.

## 실제 수행

- 코드9개를 Jetson에 배포했다. 기존4개는 교체 전에 백업했고 신규5개는 기존 파일이 없는지 확인했다. 모델·데이터·SDK·LeLab·기존 RobotController는 변경하지 않았다.
- `start_goal_agreement.py`가 현재 원시 위치를 정규화한 값과 주소67 목표를 비교한다. 원래 완전일치 진단 필드는 보존하며,1칸 및0.1단위의 두 상한과 보정 범위를 함께 검사한다.
- `reviewed_start_stop.py`는 앞선 쓰기0의 시작 HOLD 결과·POST_HOLD_STOP·기존 단발 사용 기록을 고정 해시로 검증한다. 이 검토 객체만으로 장치나 live 권한은 생기지 않는다.
- 새 `run_first_after_start_review.sh` 경로는 별도 `REARM_START` 직접 입력과 새 단발 사용 기록을 요구한다. 기존 FIELD_STOP·POST_HOLD_STOP·기존 소비 기록은 삭제하거나 덮어쓰지 않는다.
- 새 시도에서 장치 단계 중단이 나면 별도 `POST_START_REVIEW_STOP.json`을 추가하고 추가 실행을 막는다. 기존 경로는 계속 이전 POST_HOLD_STOP에서 거부된다.
- 스텝당 관절0.5도, 초당20도, 그리퍼 스텝당2포인트,30Hz, 최대10스텝/5초, 시작50도 미만·55도 중단, 과부하·home·모델·단위·보정·카메라·추론 지연·NaN 검사를 유지한다.

## 변경·백업 위치

Jetson 경로: `~/v6_process_1793887709179/operator_candidate/`.

기존4개: `trial_runtime.py`, `hold_continuation.py`, `first_trial_from_hold.py`, `test_first_trial_from_hold.py`.

신규5개: `start_goal_agreement.py`, `reviewed_start_stop.py`, `run_first_after_start_review.sh`, `test_reviewed_start_stop.py`, `test_start_goal_agreement.py`.

기존4개 백업: `~/v6_process_1793887709179/software_backups_20261108/1794125164778636699/`.

## 검증 결과

| 위치 | 명령 | 통과 | 실패·오류·제외 | 종료코드 |
|---|---|---:|---|---:|
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105/operator_candidate -p 'test_*.py'` | 162 | 0/0/0 | 0 |
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105 -p 'test_*.py'` | 15 | 0/0/0 | 0 |
| Jetson의 v6 작업 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s operator_candidate -p 'test_*.py'` | 162 | 0/0/0 | 0 |
| Jetson의 v6 작업 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s . -p 'test_*.py'` | 15 | 0/0/0 | 0 |

관련 테스트는 각각177개이며 이전155개에 신규22개를 더했다. 저장된1칸 차이는 새 검사에서 통과하고,2칸 차이·정규화0.1 초과·보정 범위 이탈·NaN·차원 오류는 거부된다. 실제 `DeviceIO.inspect_goal()`도 가짜 버스로 시험했다. 새 현장 확인, 정상10목표·토크 쓰기0, LIVE 미확인 시0목표와 추가 중단 기록을 모의 검증했다.

최초 노트북 시험의 신규3개 오류는 Windows 기본 cp949로 한글 JSON을 읽은 테스트 문제였다. 테스트에서 UTF-8을 명시한 뒤 최종162+15가 통과했다.

Jetson에서 새 래퍼의 `bash -n`, 기본 계획 출력, `--check-files`가 모두 종료코드0이었다. 파일 검사는 HOLD_FILES_READY이고 신규 검토 기록은 아직 미소비, POST_START_REVIEW_STOP은 없다. 실제 장치·추론은 이 검증에서 사용하지 않았다.

최종9개 코드 해시는 배포 목록과 일치한다. 기존 FIELD_STOP 해시 `e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c`, POST_HOLD_STOP 해시 `3ed9dc4110dfbdcfb79b352a96e3b13be3bd5ec22c758fe0efd3efea973511bf`가 유지됐다. 끝 공백 정리1건은 로직 변경 없이 백업 후 적용했고177개 모의 시험을 다시 통과했다. 최초 배포 영수증과 최종 해시의 차이는 `WHITESPACE_PATCH.json`으로 연결한다. 자세한 파일 검증과 배포 영수증은 `FINAL_VERIFICATION.json`, `APPLY_RECEIPT.json`에 보존한다.

코드 ZIP SHA256: `1ea81f19e9eaf612cfc1fd59ca3f966fd380875559414ccb796365ed33fab999`.

## 운영자가 직접 실행할 다음 명령

팔이 안정적으로 지지되어 있고 움직임을 지켜보며 DC 전원을 즉시 끌 수 있을 때 Jetson 터미널에서 실행한다. 새 경로는 토크를 다시 켜거나 끄지 않는다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_first_after_start_review.sh --live --execute --operator-present --supported --stop-ready
```

모델 로딩·예열이 끝나면 `REARM_START`를 직접 입력한다. 새 상태 검사와 카메라 준비 후 `LIVE`를 직접 입력한다. 그 뒤에만 최대10스텝/5초의 첫 동작을 시도한다.5초는 모델 준비 시간을 포함한 전체 명령 시간은 아니다.

`FIRST_TRIAL_COMPLETE`여도 첫 동작 시험이며 약통 투입 성공·G5/G6 판정이 아니다. HOLD·예외는 결과를 보존하고 그대로 재실행하지 않는다. Ctrl+C는 새 명령을 중단하고 포트를 닫으며 토크OFF를 보내지 않는다. 이미 보낸 목표에서 즉시 물리 정지를 보장하지 않으므로 예상 밖 움직임은 DC 전원을 차단하고 팔을 지지한다.

## 미확인·미실행·롤백

미확인: 이번 배포 시점의 실제 토크·온도·위치·카메라 영상, 새 경로의 실시간 지연과 실제 동작 결과. 과거 목표 초기화 뒤 토크 변화의 직접 원인도 여전히 미확인이다.

NOT_RUN: 개발자의 로봇/serial/카메라 접근, 서비스 조작, 실제 추론, 모터 쓰기, live, 학습. NOT_PUSHED.

현장 직접 입력과 실제 동작 검증은 운영자가 수행한다. 새로운 사용자 승인과 개별 중단 검토를 근거로 재개 경로를 추가했으며 기존 중단 기록을 삭제한 것은 아니다.

롤백은 프로세스가 없는 상태에서 위 백업의 기존4개 소스를 복원하고 신규 래퍼를 사용 중지한다. 코드 롤백은 토크를 끄거나 팔을 이동하지 않는다. FIELD_STOP·POST_HOLD_STOP·POST_START_REVIEW_STOP·두 사용 기록 폴더와 모든 현장 결과는 복원·삭제 대상에서 제외하고 보존한다.
