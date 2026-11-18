# 작업용50스텝 구간 준비 — 2026-11-08

사용자가 다음 작업 단계 진행을 지시해 단일50행동 chunk 실행 프로필을 구현·배포했다. 실제 작업 동작은 아직 미실행이다.

## 설정과 범위

별도 WorkSegmentLimits:50스텝, 최대10초, 관측된 현재 위치와 목표 사이 최대2도, 연속 목표 속도20도/초다. 정수 변환 여유를 빼므로 실제 소프트웨어 요청 예산은 이보다 작다. 목표 사이 속도 한계는 target_gate에서 시간 간격으로 따로 적용한다. 현재 위치 대비2도는 매33ms마다2도씩 보내겠다는 뜻이 아니다.

기존10스텝/0.5도 프로필은 유지했다. 작업 프로필도 시작50도 미만·55도 중단, 과부하·보정 범위·관절 순서/단위·모델·home·카메라·추론 deadline·NaN·큰 점프 검사를 유지한다. 높은 clip 비율은 경고로 기록한다. 기존 RobotController와 단일 포트·패킷 권한 계층, SDK의 max_relative_target=2.0은 그대로다.

가장 최근 완료된10스텝 RESULT(83ed69d8…)를 고정 해시로 확인하고 마지막 목표와 현재 피드백의 연속성을 검증한다. 기존 기록과 모든 중단을 보존한다. 새 REARM_WORK와 LIVE 직접 입력 및 별도 단발 사용 기록이 필요하다. 실패는 POST_WORK_SEGMENT_STOP.json에 추가한다.

## 검증·배포

기존5개(first_trial_core.py, first_trial_from_hold.py, reviewed_start_stop.py, first_trial.py, hold_continuation.py)를 백업 후 변경했고 신규3개(work_segment_limits.py, run_work_segment.sh, test_work_segment.py)를 추가했다. 정확한 Jetson 백업 위치는 WORK_APPLY.json의 backup_relative다.

노트북·Jetson 각각 관련216개(201+15) 모의 테스트 통과, 실패/오류/제외0, 종료코드0이다. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'이며 Jetson은 ~/venvs/smolvla-py310/bin/python을 사용했다. 신규4개는50개 목표 완료/레지스터 쓰기0, 프로필 상한 초과 거부, 첫 목표의20도/초 한계, 비정상 예측 거부를 확인했다. 모의50회 반복에서 부동소수점 오차가33.3ms 경계에 걸린 부분은1ns 수치 허용오차로 수정했고 기존 지연 거부 시험은 유지했다.

Jetson bash 문법·HOLD_FILES_READY 통과,8개 파일 해시 일치, 새 work 사용 기록 미소비다. 모델·학습 데이터·기존 SDK/LeLab 설치본과 RobotController는 수정하지 않았다.

## 현장 실행

팔과 주변 공간에 간섭이 없고 즉시 전원을 차단할 수 있을 때 Jetson에서 실행한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_work_segment.sh --live --execute --operator-present --supported --stop-ready
```

첫 질문에 REARM_WORK 한 단어만 입력한다. 이후 별도 LIVE 질문에 LIVE 한 단어만 입력한다. WORK_SEGMENT_COMPLETE는50개 목표 구간 완료이며 파지/투입 성공은 사람의 관찰이 필요하다.50개는 보통 수초 구간이고10초는 실행 상한이다. HOLD는 재실행하지 않고 출력을 전달한다.

Ctrl+C는 새 목표 중단과 포트 해제이며 토크OFF를 보내지 않는다. 이미 보낸 목표까지 움직일 수 있다. 예상 밖 움직임은 DC 전원을 차단하고 팔을 지지한다.

## 수행·미실행·롤백

수행: 코드 작성·백업·배포·모의 테스트·파일 검증. 개발자의 실제 장치/카메라/serial 접근·추론·live·학습은 NOT_RUN, push는 NOT_PUSHED다. 실제50스텝 이동량·서보 추종·파지·투입·실제 주기는 미확인이다. 단일 chunk 시험이며 장시간 자동 분류 루프는 아직 구현하지 않았다.

패키지 SHA256:d0d7706ef916121cd0f877ca3d6571aee89b35d0106420fa99b5228951837455.

롤백은 프로세스가 없는 상태에서 배포 백업의 기존5개를 복원하고 작업 래퍼를 사용 중지한다. 중단·성공·소비·계측 기록은 모두 보존한다. 코드 복원으로 토크를 끄거나 팔을 움직이지 않는다.
