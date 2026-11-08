# 정수 변환 여유를 고려한 반복 시작 보완 — 2026-11-08

새 반복은 시작 대조에서 HOLD, 목표/토크 쓰기0, probe 포트 닫힘이었다. 손목 굽힘만 반복 추종 기준0.5도를 초과했다. 현재43.472527도, 목표 피드백42.945055도, 차이-0.527473도다. 직전 요청 목표43.014385도와 현재 피드백의 차이는-0.069330도로 한 카운트0.087912도 이내다. 다른 관절도 직전 목표 일치 조건을 통과했다. 기록 당시 최고34도·상태0이고 과열/통신 실패가 이번 중단 사유는 아니다.

## 원인 근거

설치된 LeRobot SDK의 lerobot/motors/motors_bus.py:881~909를 파일로 읽었다. DEGREES 역변환은905행의 int((val * max_res / 360) + mid)이고 그리퍼 RANGE_0_100도901행에서 int()를 사용한다. 연속 실수 목표가 모터 정수 카운트로 변환되며 한 카운트 미만 차이가 추가된다. 이전 부동소수점 목표 기준으로는 약0.458도였던 손목 차이가 실제 피드백에서0.527도로 나타났다.

## 변경

직전 목표가 현재 피드백과 한 카운트 이내로 일치하는 조건을 유지한다. 이미 전송된 목표의 잔여 추종 오차에만 기존0.5도/그리퍼2포인트와 한 카운트의 인코딩 여유를 적용한다. 이 범위를 넘거나 목표가 알 수 없는 값으로 바뀌면 계속 HOLD한다.

새 목표는 반대로 더 보수적으로 계산한다. 이동량 예산에서 관절별 한 카운트, 이전/새 목표 사이 속도 예산에서 두 카운트를 예약하고 보정 범위 경계에도 한 카운트 여유를 둔다. 관절은 보통 요청 이동량0.412도 이하, 그리퍼는 약1.939포인트 이하가 되며 정수 변환 뒤 기존0.5도/2포인트 범위를 지키도록 한다. 불명확한 분해능, 부족한 속도 여유, NaN·차원 오류는 거부한다.

현재 중단 RESULT(12ae2624…)와 POST_REPEAT_LIVE_STOP(debe50ac…) 원본을 해시로 검토하고 보존한다. 새 REARM_QUANTIZED 직접 확인과 별도 단발 기록을 사용하며 실패는 POST_QUANTIZED_REPEAT_STOP.json에 추가한다. 새 목표·단위·분해능을 policy_chunk.json에 기록하고 실제 실패 관측은 repeat_continuity를 우선 저장하도록 수정했다. 기존 home·온도·과부하·추론·카메라·반복 보정 HOLD·최대10스텝/5초는 유지한다.

## 검증·배포

노트북·Jetson 각각 관련210개(195+15) 모의 테스트 통과, 실패/오류/제외0, 종료코드0이다. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'이며 Jetson은 ~/venvs/smolvla-py310/bin/python을 사용했다.

신규5개는 이번 손목 오차, 여전히 큰 오차/모르는 목표 거부, 양방향·여러 정수 위상에서 인코딩 후 이동 한계, 분해능 형식 오류, 원본 RobotController를 거친10스텝 모의 실행과 레지스터 쓰기0을 검증했다. bash 문법·HOLD_FILES_READY 검사도 종료코드0이며 새 반복은 미소비 상태다.

기존5개 변경: cadence_repeat.py, first_trial_core.py, first_trial_from_hold.py, trial_runtime.py, reviewed_start_stop.py. 신규2개: run_quantized_cadence_repeat.sh, test_encoder_margin.py. 배포7개 해시는 목록과 일치한다. 기존5개 Jetson 백업 위치는 ENCODER_APPLY.json의 backup_relative다. SDK·LeLab·모델·데이터·기존 RobotController는 수정하지 않았다.

## 다음 운영자 명령

팔을 안정적으로 받치고 즉시 전원을 차단할 수 있는 현장에서 실행한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_quantized_cadence_repeat.sh --live --execute --operator-present --supported --stop-ready
```

첫 질문에는 REARM_QUANTIZED 한 단어만, 이후 별도의 LIVE 질문에는 LIVE 한 단어만 입력한다. 최대10스텝/5초 시험이며 약통 파지·투입 성공 판정은 아니다. HOLD는 재실행하지 않고 출력을 전달한다. Ctrl+C는 새 명령을 중단하고 포트를 닫으며 토크OFF를 보내지 않는다. 예상 밖 움직임은 DC 전원을 차단하고 팔을 지지한다.

## 실제 수행·미실행

실제 수행: SSH 저장 파일/SDK 소스 읽기, 코드 작성·백업·배포, 모의 테스트·파일 검증. 개발자의 실제 장치/카메라/serial 접근·추론·live·학습은 NOT_RUN이고 push는 NOT_PUSHED다. 새 실제 인코딩 결과·실제 주기·새 동작 성공은 아직 미확인이다. 수학적 인코딩 모의 시험을 실기 검증으로 쓰지 않는다.

패키지 SHA256:68e73bb90c397b8b76a179b4cab117d8016bb909c85f4f4922586146b0a23eb3.

롤백은 프로세스가 없는 상태에서 배포 백업의 기존5개를 복원하고 신규 래퍼를 사용 중지한다. 모든 중단·성공·소비·측정 기록은 보존한다. 코드 복원으로 모터를 움직이거나 토크를 끄지 않는다.
