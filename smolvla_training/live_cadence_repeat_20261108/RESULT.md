# 읽기 주기 개선 확인·짧은 live 반복 준비 — 2026-11-08

운영자의 비교 계측은 READ_ONLY_TIMING_COMPLETE, 모터/토크 쓰기0, 포트 닫힘으로 완료됐다. 기준 읽기 주기16.931Hz에서 작업 후 남은 시간만 기다린 결과29.703Hz로 개선됐다. 이는 SDK 목표 전송이 없는 읽기 계측 결과이며 실제 live30Hz 달성 근거는 아니다.

## 확인 수치

기준 marker9구간0.531562초, 비교0.302995초다. 작업시간 평균은26.58ms에서27.20ms로 비슷하고 평균 대기만30.07ms에서6.80ms로 줄었다. 비교 회차 원본은 ~/control_timing_1794130957872490741/TIMING_RESULT.json, SHA256은90e6ba8ac729421074d0ef03d7c50ddc98997c61b830a74108bdcf01d05ff316이다.

## 코드 변경

새 --repeat-live 경로에만 작업 먼저/남은 시간 대기 방식을 적용했다. 기존 실행 경로의 대기 방식은 유지한다. 매 스텝 카메라·snapshot·기록 검증·대기·SDK 목표 전송 시간을 step_timings.json에 저장하고 실패 회차도 남긴다. 작업이 길어지면 늦게 보낼 수 있지만 따라잡기 위해 몰아 보내지 않는다. 전송 전에 관측이 한 주기보다 오래됐으면 HOLD한다.

첫 성공 시험의 마지막 전송 목표를 고정 해시로 불러와 현재 주소67 피드백과 한 모터 카운트의 양자화 범위 안에서 일치하는지 확인한다. 현재 위치와 목표의 추종 오차는 관절0.5도/그리퍼2포인트 이내여야 한다. 이 검사는 반복 경로에만 적용되며 기존 시작 검사1칸/0.1도 결과도 별도 진단으로 보존한다. home·보정 범위·단위·모델·온도·과부하·카메라·추론 지연·반복 보정 HOLD는 유지한다.

완료된 첫 live, 두 읽기 계측, 이전 중단과 소비 기록을 보존한다. 새 REARM_REPEAT와 LIVE 직접 입력, 별도 단발 소비를 요구한다. 이번 반복 중단은 POST_REPEAT_LIVE_STOP.json에 보존한다. 최대10스텝/5초·관절0.5도·초당20도·그리퍼2포인트를 유지한다. 기존 RobotController·SDK·LeLab·모델·데이터는 수정하지 않았다.

기존4개: first_trial_core.py, first_trial_from_hold.py, trial_runtime.py, reviewed_start_stop.py. 신규3개: cadence_repeat.py, run_cadence_live_repeat.sh, test_live_cadence.py. 정확한 Jetson 백업 위치는 LIVE_CADENCE_APPLY.json의 backup_relative이며 배포 전에 기존4개를 보존했다.

## 검증

노트북·Jetson 각각 관련205개(190+15) 모의 테스트 통과, 실패/오류/제외0, 종료코드0. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'이며 Jetson은 ~/venvs/smolvla-py310/bin/python을 사용했다.

신규5개는 개선 주기·느린 작업 시 몰아보내기 금지·대기 지연 시 전송 전 HOLD·직전 안전 목표의 연속성·알 수 없는 목표 및 과한 추종 오차 거부를 검증한다. 카메라 갱신/감시 작업이 큰 스텝은 한 주기를 넘을 수 있으므로 전 스텝 정확30Hz를 보장하지 않는다. 첫 모의 시험의 카메라 갱신 스텝을 정확33.3ms로 기대한 assertion을 실제 감시 비용에 맞춰 수정했다. 제어 검사를 줄인 수정은 아니다.

Jetson의 bash 문법과 --check-files는 종료코드0, HOLD_FILES_READY이며 새 반복 기록은 미소비 상태다. 배포7개 해시가 목록과 일치한다. 이번 개발 작업의 실제 장치/serial/카메라 접근·추론·live·학습은 NOT_RUN이고 push는 NOT_PUSHED다.

## 운영자의 다음 명령

팔을 안정적으로 받치고 즉시 DC 전원을 끌 수 있는 현장에서 실행한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_cadence_live_repeat.sh --live --execute --operator-present --supported --stop-ready
```

첫 확인 질문에는 REARM_REPEAT 한 단어만 입력한다. 이후 별도로 LIVE 질문이 뜰 때 LIVE 한 단어만 입력한다. 완료 또는 HOLD 출력을 전달하면 저장된 실제 주기와 SDK 전송 시간을 검토한다. HOLD는 재실행하지 않고 기록을 보존한다. Ctrl+C는 새 명령을 중단하고 포트를 닫으며 토크OFF는 보내지 않는다. 예상 밖 움직임은 DC 전원을 차단하고 팔을 지지한다.

이 반복은 짧은 live 시험이며 파지·바구니 투입 성공이나 G5/G6 판정이 아니다. 새 실제 전송 주기는 미확인이다. 패키지 SHA256은758780e950253a33e8a1e09c614c00f8237e8bf20d019152dfb9d792c6f531e0이다.

롤백은 프로세스가 없는 상태에서 배포 백업의 기존4개를 복원하고 새 래퍼를 사용 중지한다. 코드 복원은 토크를 끄거나 모터를 움직이지 않는다. 모든 중단·성공·소비·계측 기록은 보존한다.
