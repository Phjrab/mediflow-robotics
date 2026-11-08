# 관절 범위 HOLD 분석과 목표 변환 보완 — 2026-11-08

운영자의 새 시도는 시작 위치 검사를 통과하고 LIVE 확인 후 관절 범위 검사에서 멈췄다. 목표·토크 쓰기0, 실행0스텝, io/probe 포트 닫힘, 기존 중단 기록 보존이다. 이번 후속 작업에서는 저장 파일과 오프라인 정책 계산만 사용했다.

## 확인된 사실과 한계

- 원래 실행은 `prepared_no_write`까지 진행했다. 최종 사유는 `관절 한계/형식 이탈`이며 당시 실패값은 기록되지 않았다. 어느 관절의 예측값/현재값/이전 목표가 직접 조건을 넘었는지는 UNVERIFIED다.
- 기존 `target_gate()`는 정책 예측·현재 자세·이전 목표에 모두 보정 범위 검사를 먼저 적용한 뒤 스텝/속도 제한을 계산했다. 따라서 범위 밖의 작은 정책 예측도 전송 목표로 줄이기 전에 거부했다.
- 저장된 시작 이미지3장과 inspection.state로 정책을 seed0·1·2에서 각각 다시 계산했다. 이는 실제 실패 순간의 입력 프레임/관절값/난수 상태를 그대로 재현한 시험이 아니다. 원래 첫 예측값을 복원했다고 주장하지 않는다.

| 오프라인 seed | 추론 시간 | 50개 중 범위 초과 | 첫10개 중 초과 | 최대 초과 |
|---:|---:|---:|---:|---:|
| 0 | 1.4453초 | 0 | 0 | 0도 |
| 1 | 1.4178초 | 10 | 3 | 팔꿈치1.1027도 |
| 2 | 1.4032초 | 2 | 2 | 팔꿈치0.5920도 |

팔꿈치 보정 상한은97.010989도이며 예를 들어 seed1의2번째 인덱스 예측은97.179565도였다. 각 실행의 첫 예측 자체는 기존 gate를 통과했다. 따라서 범위 밖 정책 예측 현상은 확인했지만 원래0스텝 중단의 정확한 첫 값은 여전히 미확인이다.

추론 측정은 저장 프레임3회이며 기존 provider 예열2회는 별도다. 측정 평균1.4221초·최대1.4453초다. 원래 실행의 지연이나 로봇 성공률 근거가 아니다. 정책 자식 프로세스는 종료됐고 모터 명령은0이다.

저장된 손목·Astra·RealSense 이미지를 시각 확인했으며3장 모두 빈 영상이 아니었다. Astra 색 불일치는 알려진 상태다. RealSense 시작 경고의 원인과 연속 프레임 상태는 이 시험에서 검증하지 않았다.

## 수정·배포한 것

- 현재 자세와 이전 전송 목표는 계속 보정 범위를 벗어나면 HOLD한다.
- 유한한6축 정책 예측은 기존 보정 범위·스텝당0.5도·초당20도 제한의 교집합으로 잘라 전송 목표를 만든다. 그리퍼0~100, 스텝당2포인트,10단위 초과 큰 점프 거부도 유지한다. 범위 밖 예측을 그대로 보내지 않는다.
- 반복 보정 비율, 온도, 과부하, home, 카메라, 모델/단위/보정, 추론 deadline, 최대10스텝/5초 조건은 유지한다. 반복 보정이 많으면 실제로 몇 스텝 보낸 뒤에도 HOLD할 수 있다.
- 추론한50개 전체를 `policy_chunk.json`에 별도 저장한다. 전송 전 실패에도 원시 예측·모델 입력 state·한계·추론 시간을 보존한다. 실패 시 관절명/입력 종류/값/범위와 마지막 목표 계산 입력도 RESULT에 기록한다.
- 새 `run_first_after_action_review.sh`는 쓰기0의 이번 중단과 앞선 두 중단의 원본 해시를 확인한다. 기존 파일과 소비 기록은 보존하고 새 REARM_ACTION 및 LIVE 직접 입력을 요구한다. 새 시도 중단은 `POST_ACTION_REVIEW_STOP.json`에 추가한다.

변경5개: `first_trial_core.py`, `first_trial_from_hold.py`, `hold_continuation.py`, `reviewed_start_stop.py`, `test_first_trial_from_hold.py`.

신규3개: `run_first_after_action_review.sh`, `test_action_projection.py`, `test_reviewed_action_stop.py`.

Jetson 위치는 `~/v6_process_1793887709179/operator_candidate/`다. 기존5개 백업은 `~/v6_process_1793887709179/software_backups_20261108/1794126733003008737/`에 있다. 모델·데이터·SDK·LeLab·기존 RobotController는 수정하지 않았다.

## 실제 검증

| 위치 | 명령 | 결과 | 종료코드 |
|---|---|---|---:|
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105/operator_candidate -p 'test_*.py'` | 176통과, 실패/오류/제외0 | 0 |
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105 -p 'test_*.py'` | 15통과, 실패/오류/제외0 | 0 |
| Jetson v6 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s operator_candidate -p 'test_*.py'` | 176통과, 실패/오류/제외0 | 0 |
| Jetson v6 폴더 | `~/venvs/smolvla-py310/bin/python -B -m unittest discover -s . -p 'test_*.py'` | 15통과, 실패/오류/제외0 | 0 |

관련191개는 기존177개와 신규14개다. 저장 예측30개의 투영 목표가 물리 범위·이동 제한 안인지 확인했고, 현재/이전 목표 범위 이탈·큰 점프·NaN·차원 오류는 거부했다. 새 현장 토큰·원본 보존·단발 사용·정상10목표/토크 쓰기0·실패 예측 저장도 모의 검증했다.

새 래퍼의 bash 문법·기본 PLAN_ONLY·파일만 검사하는 `--check-files`가 종료코드0이다. 새 action 검토는 아직 미소비이고 추가 POST_ACTION_REVIEW_STOP은 없다. 배포8개 코드 해시가 목록과 일치한다.

오프라인 분석 도구 첫 버전은 의존 경로 초기화 누락으로 모델 로드 전 종료됐다. 기존 의존 폴더를 먼저 준비하는 v2로 수정해 분석3회와 프로세스 종료를 확인했다. 기존 환경 설치·수정은 없었다.

## 다음 현장 명령

팔을 안정적으로 받치고 동작을 지켜보며 DC 전원을 즉시 끌 수 있을 때 Jetson 터미널에서 직접 실행한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_first_after_action_review.sh --live --execute --operator-present --supported --stop-ready
```

모델 준비 후 `REARM_ACTION`, 카메라·상태 확인 후 `LIVE`를 직접 입력한다. 최대10스텝/5초의 첫 동작이며 약통 투입 성공이나 G5/G6 판정이 아니다. HOLD가 나오면 재실행하지 말고 결과를 보존한다.

Ctrl+C는 새 명령을 중단하고 포트를 닫으며 토크OFF는 보내지 않는다. 이미 전달된 목표에서 즉시 물리 정지를 보장하지 않는다. 예상 밖 움직임은 DC 전원을 차단하고 팔을 지지한다.

## 기록·미실행·롤백

FIELD_STOP(e8a2bf9e…), POST_HOLD_STOP(3ed9dc41…), POST_START_REVIEW_STOP(c70c2909…)와 이전 소비 기록을 모두 보존했다. 새 패키지 SHA256은 `9e4a62d9ff3e18b7537889f0b235b8ca33816fd219e239a65c045c1824994ade`다.

ACTUAL_EXECUTED: SSH 파일 읽기·복사, 저장 이미지 시각 확인, 오프라인 추론3회와 예열, 코드 백업/배포, 모의 테스트, 파일 검증. NOT_RUN_BY_AGENT: 실제 로봇/serial/카메라 열기, 서비스 조작, 모터 쓰기, live, 학습. NOT_PUSHED.

미확인: 원래 실패 순간의 값, 새 실제 동작 결과와 실제 카메라 연속성. 다음 물리 작업과 새 확인 입력은 운영자가 직접 한다.

롤백은 프로세스가 없는 상태에서 위 백업의 기존5개를 복원하고 신규 래퍼를 사용 중지한다. 코드 복원은 토크를 끄거나 팔을 움직이지 않는다. 모든 중단 파일·소비 기록·현장 결과는 삭제하거나 되돌리지 않는다.
