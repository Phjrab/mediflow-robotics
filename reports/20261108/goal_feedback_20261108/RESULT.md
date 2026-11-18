# 현재 목표 피드백 확인과 토크 유지 시험 준비 — 2026-11-08

## 사용자 실측과 SSH 검수

운영자가 직접 실행한 진단 `1794116904918839503`을 SSH로 읽고 원시 응답과 대조했다. 원본 진단은 수정하지 않았으며 검토 사본만 보관했다.

- `RESULT.json` SHA256: `90ab6aec722a585b1fa3f83b8888d36a60b5d14608b20586c5e781bb0d578ada`.
- READ 패킷 66개, 차단 0, 모터 레지스터 쓰기 0, 포트 닫힘, 정리 오류 0.
- 토크 6개 OFF, 위치 모드 6개 0. 온도는 27~32도였다.
- 몸체 5개 관절의 home 차이는 모두 5도 이내였다. 이 값은 저장 당시의 측정이며 현재 실시간 상태를 대신하지 않는다.

| 관절 | 현재 위치 주소56 | 목표 제어 주소42 | 현재 목표 피드백 주소67 | 56=67 |
|---|---:|---:|---:|---|
| shoulder_pan | 2034 | 0 | 2034 | 일치 |
| shoulder_lift | 830 | 0 | 830 | 일치 |
| elbow_flex | 3082 | 0 | 3082 | 일치 |
| wrist_flex | 2576 | 0 | 2576 | 일치 |
| wrist_roll | 1812 | 0 | 1812 | 일치 |
| gripper | 531 | 0 | 531 | 일치 |

공급사의 [ST 메모리 표](https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation)는 주소42를 목표 제어값, 주소67을 읽기 전용 현재 목표 피드백으로 구분한다. 제조사 [SDK 정의](https://github.com/ftservo/FTServo_Arduino/blob/main/src/SMS_STS.h)도 쓰기 목표 주소42를 정의한다. 둘을 같은 필드로 취급하지 않는다.

이번 진단에서는 응답 오류나 home 이탈이 관찰되지 않았다. 과거 Goal 쓰기 뒤 토크가 변한 직접 원인은 여전히 미확인이다. 이 결과만으로 모터 고장 여부, 토크ON 시 동작, VLA 성공을 확정하지 않는다.

## 수정한 코드와 유지한 차단

- `arm_source.py`: 기존 목표 대조에는 주소67을 사용하고 주소42도 별도 기록한다. Goal_Position 쓰기는 여전히 금지한다. 현재 위치가 실제 보정 범위 안인지 확인하고, 토크·위치모드·온도·home·과부하 검사 및 보정·모델·단위 확인을 유지한다.
- `individual_reply_guard.py`: 개별 READ의 원시 ID·길이·헤더·체크섬·오류 상태를 SDK가 데이터를 자르기 전에 검증한다. 오류 뒤 추가 통신을 차단하고 설치본 파일은 수정하지 않는다.
- `operator_arm.py`, `trial_runtime.py`: 현재 목표를 주소67로 대조하도록 정리했다. 정책 재무장 및 live의 기존 FIELD_STOP 차단은 유지한다. 일반 경로로 재시도하거나 중단 파일을 삭제하지 않는다.
- `review_saved_readiness.py`: 확인된 목표 피드백과 미검증 토크ON 전환을 구분한다. `live_authorized=false`, `rearm_authorized=false`를 유지한다.
- `run_target67_hold.sh`: 기본은 PLAN_ONLY다. 별도 현장 토크 유지 시험만 기존 수동 토크 도구로 연결한다. VLA 정책 경로가 아니며 G5/G6 통과로 기록하지 않는다.

기존 토크 도구의 직접 enable도 FIELD_STOP이 있으면 기본 거부한다. 별도 시험은 검증된 새 읽기 결과와 원래 중단 해시가 맞고, 운영자가 `REVIEW_HOLD` 및 `ENABLE`을 직접 입력할 때만 진행한다. 확인은 모터 연결 전에 이뤄진다. 새 승인 영수증의 범위는 **TORQUE_HOLD_ONLY**이며 live 허가로 사용하지 않는다. 원래 중단 기록은 그대로 남긴다.

토크ON 때 팔이 움직일 가능성은 아직 미확인이다. 이 시험은 그 전환을 현장에서 관찰하기 위한 것으로, 안전한 동작이 이미 입증됐다는 뜻이 아니다.

## 실제 수행한 소프트웨어 검증

| 위치 | 명령·검사 | 결과 |
|---|---|---|
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105/operator_candidate -p 'test_*.py'` | 106 통과, 실패·오류·제외0, exit0 |
| 노트북 | `python -B -m unittest discover -s reports/v6_process_isolation_20261105 -p 'test_*.py'` | 15 통과, 실패·오류·제외0, exit0 |
| 노트북 | 실제 ZIP의 가짜 작업 폴더 적용·백업·재적용 거부 | 별도2건 통과, exit0 |
| Jetson | 위 operator_candidate 및 루트 관련 테스트 | 106+15=121 통과, exit0 |
| Jetson | 설치된 SDK `readTxRx` 소스와 가짜 정상 응답/짧은 ACK | 별도2건 통과, 실제 통신0 |
| Jetson | 셸 문법 검사·토크 도구 기본 PLAN_ONLY·정책 기본 PLAN_ONLY | 모두 exit0, 장치 연결0 |
| Jetson | 저장된 원시 응답 기반 검토 | 목표 피드백 일치 확인, 토크ON·live 허가 없음 |

설치 SDK 파일 SHA256은 `9932c85b9e2ac671a7e33f39e8e80b05d6592280d9f2bcebd85103845e36e655`로 이전 검토본과 같았다. SDK 함수는 AST로 읽어 가짜 응답에만 사용했다. GPU 모델 추론·serial 열기는 실행하지 않았다. 전체 프로젝트 회귀와 실기 성능 검증을 뜻하지 않는다.

## 배포·보존

기존 코드6개를 기준 해시 확인 후 백업하고 수정 파일10개를 배포했다.

- 백업: `~/v6_process_1793887709179/software_backups_20261108/1794118259820489988/`
- 코드 ZIP SHA256: `c7907ef8fab198f80be74c161725e87e658f547ded2155df2c1d8ba14276280c`.
- FIELD_STOP 배포 전·후·검증 후 SHA256: `e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c`.
- 기존 RobotController, LeLab, SDK, 가상환경, 모델·데이터 원본은 변경하지 않았다.

## 사용자가 현장에서 할 다음 한 줄

팔을 지지대로 안정되게 받치고 즉시 DC 전원을 차단할 수 있을 때만 선택한다. follower DC 전원이 이미 켜져 있으면 그대로 둔다. 이번 명령은 **실제로 모터 토크를 켜는 별도 시험**이다.

```bash
sudo -v && bash ~/v6_process_1793887709179/operator_candidate/run_target67_hold.sh --execute --review-stop-for-hold-only
```

1. 위험·중단 기록 검토에 동의하면 운영자가 `REVIEW_HOLD`를 직접 입력한다.
2. 실제 토크ON 시험을 시작하려면 운영자가 `ENABLE`을 직접 입력한다. 개발자는 두 입력을 대신하지 않는다.
3. 도구는 새 상태·주소67·보정·온도·home·포트 비점유·LeLab inactive를 확인하고, 토크ON 패킷1개만 보낸다. 목표 위치·모드·보정 쓰기는0이다.
4. 토크ON 후5회/약0.2초 동안 토크·모드·온도·부하·위치 변화를 확인한다. 자세0.5단위 초과 변화나 이상 응답이면 추가 쓰기 없이 종료한다. 몸체는 도, 그리퍼는0~100 단위다.
5. 팔이 예상 밖으로 움직이거나 밀어붙이면 **즉시 DC 전원을 차단**한다. Ctrl+C는 추가 명령과 프로그램을 중단하고 포트를 닫지만 토크를 자동으로 끄지 않는다. 종료 후 토크ON이 남을 수 있으므로 팔을 손으로 억지로 움직이지 않는다.

정상 결과는 `TORQUE_ON_HOLD`, `authorized_writes=1`, `goal_writes=0`, `port_closed=true`다. 이는 짧은 토크 유지 시험 결과이며 VLA 실행이나 성공률 증거가 아니다. 결과가 나오면 개발자가 SSH로 새 로그를 읽고 다음 단계를 판단한다. 이번 도구의 영수증만으로 정책 live 차단을 해제하지 않는다.

장치를 열지 않는 재개 명령:

```bash
bash ~/v6_process_1793887709179/operator_candidate/run_target67_hold.sh
```

## NOT_RUN / 롤백

개발자가 실제 모터를 열거나 레지스터를 읽고 쓴 실행, 토크ON 시험, 카메라 접근, 모델 추론, 학습, 재무장·live, push는 모두 NOT_RUN이다. 사용자의 실측 진단과 개발자의 모의 검증을 구분했다.

롤백은 위 백업의 기존6개 코드만 복원한다. 신규 보조 파일은 사용하지 않은 채 남겨도 된다. FIELD_STOP·진단·모델·데이터는 삭제하거나 되돌리지 않는다.
