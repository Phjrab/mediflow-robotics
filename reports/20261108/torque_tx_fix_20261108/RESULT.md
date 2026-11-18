# 토크 송신 전 소프트웨어 차단 수정 — 2026-11-08

## 이번 실패의 원인과 실제 송신 여부

사용자의 `torque_guard_20261108_153016/events.jsonl`을 SSH로 읽고 사본을 보존했다. 이번 시험의 실패는 새 응답 보호 코드의 호환성 오류다. SDK의 `syncWriteTxOnly()`도 내부에서 `txRxPacket()`을 호출하는데, 보호기가 그 진입점을 개별 READ 전용으로 처리해 토크 명령을 거부했다.

- 이벤트9개, `AUTHORIZED_WRITE` 0개, `WRITE_TRANSMITTED` 0개.
- 저장된 `authorized_writes=0`, `goal_writes=0`과 일치한다. 이번 시험에서는 모터 레지스터 쓰기를 송신하지 않았다.
- 마지막 저장 토크 응답은6개 모두OFF, 포트 닫힘, FIELD_STOP 보존을 확인했다. 이는 시험 당시 값이며 현재 상태를 추정하지 않는다.
- 오류 원문과 원시 응답을 삭제하지 않았다. 이번 소프트웨어 오류는11월6일 원래 목표 쓰기 뒤 토크 불일치의 직접 원인을 확정하거나 해소한 증거가 아니다.
- 오류 로그 SHA256: `2e52d88b32542047e763527cf562b10db16ff207b397cbe3e404b2e4c116517e`.

이전 모의 검증은 개별 READ와 가짜 bus 중심이어서 SDK의 실제 송신 함수 연결을 빠뜨렸다. 이를 이번에 추가했다.

## 수정

개별 READ는 ID·길이·헤더·체크섬·상태 검증을 계속한다. 비READ 요청은 기본 거부하며, 다음 조건이 모두 맞는 토크 동기 쓰기만 기존 SDK에 전달한다.

1. 기존 `WriteGuard`가 실제 포트의 송신 함수에 설치돼 있고 동일 포트·스레드의 명시적 허가가 활성화돼 있을 것.
2. 주소40, 폭1, ID1~6과 현재 enable/disable 모드의 값이 정확히 일치할 것. enable은 기존 목표 검증도 완료해야 한다.
3. SDK가 만들 최종 패킷이 기존 가드의 일회성 허가 패킷과 완전히 같을 것.
4. 실제 하위 송신 가드가 그 허가를 정확히1번 소비했음을 확인할 것. 반복·부분 송신·오류 뒤 재시도는 거부한다.

broadcast 쓰기는 응답이 없는 정상 경로이므로 READ 응답 검사를 적용하지 않는다. 기존 송신 가드가 실제 전송을 계속 검사·계수하며, 목표42·모드33·임의 레지스터 쓰기와 무허가 토크는 허용하지 않는다. 모터 모델·보정·home·온도·과부하·이동 제한 및 live 중단 조건은 유지한다.

수정 코드3개는 `individual_reply_guard.py`, `arm_source.py`, `operator_arm.py`다. 기존 코드3개를 백업하고 SDK 연결 테스트와 함수 fixture를 포함한5개 파일을 배포했다. SDK·LeLab·가상환경·모델·데이터·RobotController는 수정하지 않았다.

## 실제 수행한 검증

- 설치 SDK와 같은 SHA256의 실제 `syncWriteTxOnly → txRxPacket → txPacket` 및 `readTxRx` 소스를 가짜 포트·실제 WriteGuard에 연결한 신규 모의 테스트11개 통과.
- 정상 허가의 가짜 토크ON/OFF는 각각1패킷, 무허가·목표 쓰기·모드 쓰기·잘못된ID/값은0패킷, 재시도·부분 송신 후 추가 전송 금지, 송신 후 READ 검증 유지 등을 확인했다.
- 노트북 및 Jetson 각각 관련 모의 테스트132개(117+15) 통과, 실패·오류·제외0, 두 명령 모두 exit0.
- Jetson에서 셸 문법 검사와 두 도구 기본 PLAN_ONLY 통과. 이 실행은 장치를 열지 않는다.
- SDK 원본 SHA256 `9932c85b9e2ac671a7e33f39e8e80b05d6592280d9f2bcebd85103845e36e655`와 FIELD_STOP SHA256 `e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c`는 그대로다. LeLab은 inactive였다.

명령은 `python -B -m unittest discover -s operator_candidate -p 'test_*.py'`와 루트의 `python -B -m unittest discover -s . -p 'test_*.py'`다. 전체 프로젝트 회귀나 실제 모터 송신 성공을 뜻하지 않는다.

## 다음 운영자 시험

목표 쓰기0·토크 유지 전용 시험이다. **수정 후에는 실제 토크ON 패킷이 전달될 수 있으므로**, 팔을 지지대에 안정되게 받치고 즉시 DC 전원을 끌 수 있을 때만 선택한다. 전원이 이미 켜져 있으면 그대로 둔다.

```bash
sudo -v && bash ~/v6_process_1793887709179/operator_candidate/run_target67_hold.sh --execute --review-stop-for-hold-only
```

운영자가 안내를 읽고 `REVIEW_HOLD`, 이어서 `ENABLE`을 직접 입력한다. 개발자는 확인 입력이나 실제 토크 시험을 대신하지 않았다. 예상 밖 움직임·밀어붙임이면 즉시 DC 전원을 차단한다. Ctrl+C는 프로그램과 추가 명령을 중단하지만 토크를 끄지 않는다.

정상 예상 출력은 `TORQUE_ON_HOLD`, `authorized_writes=1`, `goal_writes=0`, `port_closed=true`다. 이 출력은 아직 실측하지 않았으며 VLA live·약통 투입 성공으로 쓰지 않는다. 결과가 나오면 개발자가 SSH로 새 로그를 읽는다.

장치 미사용 재개 명령은 `bash ~/v6_process_1793887709179/operator_candidate/run_target67_hold.sh`다.

## NOT_RUN / 백업·롤백

개발자의 실제 모터·serial·카메라 접근, 토크 시험 재실행, 모델 추론·학습, 재무장·live, push는 NOT_RUN이다. 정책 live의 원래 중단 기록 차단과 운영자 직접 확인은 유지한다.

Jetson 백업은 `~/v6_process_1793887709179/software_backups_20261108/1794120073261910859/`다. 롤백은 그곳의 기존3개 코드만 복원한다. 신규 테스트 파일은 사용하지 않은 채 남겨도 된다. 오류 로그·FIELD_STOP·모델·데이터는 삭제하거나 되돌리지 않는다.
