# 제어 주기 읽기 계측 준비 — 2026-11-08

첫 live에서9개 목표 간격으로 계산한 전송 빈도는15.91Hz이고 설정은30Hz였다. 현 코드는 주기 대기 뒤 카메라·센서·기록 검증을 수행해 그 시간이 목표 전송 간격에 추가된다. 각 작업의 실제 시간 비중은 아직 미측정이다.

## 만든 것과 검증

profile_control_timing.py와 run_control_timing.sh를 추가했다. 기본은 PLAN_ONLY, --check-files는 파일만 검사한다. 운영자가 직접 실행하면 센서 읽기·카메라 갱신·기록 검증·대기를10회 계측하고 TIMING_RESULT.json에 저장한다. 추론과 send_action은 호출하지 않는다. SDK 목표 전송 시간은 측정하지 않아 goal_send_seconds=null로 표시한다. 읽기 계측의 marker 주기를 실제 모터 제어 주기로 해석하지 않는다.

첫 live 성공 RESULT와 소비 기록의 고정 해시를 검증하고 새 READ_TIMING 직접 입력·별도 단발 기록을 요구한다. 기존 중단 파일과 소비 기록은 보존한다. profile_only 경로에서 allow_goal=True는 장치 연결 전에 거부하고 실제 PacketPermission도 쓰기를 허용하지 않는다. 온도·토크·센서 검사는 유지한다. 모터 토크를 변경하지 않고 종료 시 포트를 닫는다.

기존3개 변경: reviewed_start_stop.py, hold_continuation.py, trial_runtime.py. 신규3개: profile_control_timing.py, run_control_timing.sh, test_control_timing.py. 기존3개 Jetson 백업은 ~/v6_process_1793887709179/software_backups_20261108/1794129964032061160/ 이다.

노트북·Jetson 각각 관련196개(181+15) 테스트 통과, 실패/오류/제외0, 종료코드0이다. 신규5개에서 섹션 시간 분리·10회 읽기·목표 호출 금지·과열 거부·프로파일 권한으로 목표 쓰기 거부·새 현장 확인·한 번만 사용을 검증했다. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'이며 Jetson에서는 ~/venvs/smolvla-py310/bin/python을 사용했다. bash 문법·PLAN_ONLY·TIMING_FILES_READY 파일 검사도 종료코드0이다.6개 코드 해시는 배포 목록과 일치한다.

## 운영자가 다음에 할 일

아래 명령은 모터를 움직이지 않는 읽기 계측이다. 운영자가 로봇 상태를 지켜보는 현장에서 Jetson 터미널에 붙여 넣는다. 기존 LeLab은 종료 상태여야 한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_control_timing.sh --execute --operator-present
```

READ_TIMING 입력 질문이 뜨면 READ_TIMING 한 단어만 입력한다. READ_ONLY_TIMING_COMPLETE가 나오면 출력 경로를 전달한다. HOLD는 기록을 보존하고 재실행하지 않는다. Ctrl+C로 중단할 수 있으며 토크OFF를 보내지 않는다.

## 실제 수행·남은 일

실제 수행: 코드 작성·백업·배포, 모의 테스트, 파일 검증, 기존 성공 파일 읽기. 이번 작업의 실제 장치/카메라/serial 접근·추론·live·학습은 NOT_RUN. push는 NOT_PUSHED다. 이번 단계에서 실제30Hz 달성이나 속도 개선을 주장하지 않는다.

다음은 운영자의 읽기 계측 결과를 받아 센서·카메라·파일 검증 시간을 분리하고 주기 대기 위치를 조정하는 것이다. 그 뒤 짧은 live 반복 경로를 준비한다. 현재 live 성공 명령의 소비 기록은 유지하며 새 live 반복은 아직 준비/실행하지 않았다.

롤백은 프로세스가 없는 상태에서 위 백업의 기존3개를 복원하고 계측 래퍼를 사용 중지한다. 모든 중단·성공·소비·계측 기록은 보존한다. 코드 복원은 토크를 끄거나 모터를 움직이지 않는다.
