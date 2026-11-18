# 팔꿈치 쪽 연결용 선택 측정 준비

2026-11-12. 사용자가 제어보드 → 팔꿈치 쪽 비교를 먼저 요청했다. 최신 설명은 팔꿈치–손목 선의 손목 끝을 제어보드에 연결했다는 것이다. 같은 선의 반대쪽은 팔꿈치에 연결되어야 이 구성이 된다. 그리퍼–손목 선의 손목 끝, 원래 어깨–보드 선의 보드 끝은 분리 상태로 둔다. DC OFF를 유지한 채 소프트웨어만 준비했으며 실제 연결 ID와 측정 결과는 UNVERIFIED / NOT_RUN이다.

## 만든 도구와 실제 확인

- 새 소스: `selected_chain_voltage_temperature_readonly.cpp`.
- 소스 SHA256: `efc01748ddb959048062fe8984ad9acaf2168c20eaff164d0a72ecd749d388cb`.
- Jetson 새 폴더: `~/vendor_temp_isolation_20261111/selected_chain_ready_rc7_srqm/`.
- 바이너리 SHA256: `26da6a746434823a43e2bb6721d28a07fdc9e382f69c56e50a69fe09643c8a83`.
- 기존 g++ 11.4로 컴파일 exit 0. 제조사 SCS.cpp/SCSerial.cpp/SMS_STS.cpp 및 기존 venv는 수정하지 않았다.
- `probe --mock`: 39개 통과, exit 0, 장치 열기 없음.
- 가상 터미널 시험 12개 통과. 실제 모터 대신 새 `/dev/pts/` 끝점만 사용했다. 정상 응답, 완전 무응답, 부분 프레임, 다른 ID, 체크섬, 상태 오류, 토크 ON, 55°C, 포트 단절, 첫 성공 뒤 무응답, 남은 수신 바이트, 선택 ID 연속 측정을 검사했다.
- 선택 ID 1·3·4 가상 측정은 20초 동안 1,872개 READ만 전송했다. 실제 모터 ID를 1·3·4로 판정한 것이 아니다.
- 기록: 원격 `compile.log`, `mock.log`, `PTY_RESULTS.json`, `PREPARED.json`, `pty_checks_*/`.
- 컴파일 경고는 제조사 미사용 setBaudRate의 변수 초기화 경고와 모의 응답 벡터 삽입 경고를 포함한다. 실제 도구는 초기화된 begin(1000000) 경로를 쓰며 가상 포트 시험을 통과했다.
- 준비 당시 LeLab inactive, Astra active 확인. 서비스 변경 없음. 실제 serial·카메라 열기, 모터 레지스터 쓰기, 추론, live, push는 NOT_RUN. 준비 완료 후 SSH 종료.

## 읽기 절차

DC ON을 운영자가 확인한 뒤, 연결 ID를 추측하지 않고 `--probe-id`로 한 번에 한 ID만 확인한다. 실제 선택 측정은 응답한 ID 목록만 지정한다. READ 주소 40/1, 62/1·2, 63/1만 허용하며 목표·토크·설정 쓰기를 전송 직전에 차단한다. 토크 ON, 55°C, 상태/프레임/운영체제 오류이면 중단한다.

첫 Torque_Enable 조회의 완전 무응답만 정상 시간 만료·송신 완료·수신 0바이트·OS 오류 없음·중단 신호 없음을 함께 확인해 `NO_RESPONSE_UNVERIFIED`(exit 3)로 구분한다. 미연결이라고 확정하지 않는다. 성공 응답 뒤 무응답이나 연속 측정의 무응답은 HOLD(exit 1)다. 재시도나 수신 flush는 없다. 조회 실행 간 연속 무응답 수는 총괄 실행 기록에 남긴다.

명령 형태(이 문서에서는 실제 실행하지 않음):

```text
probe --probe-id ID --port PORT --out NEW_JSONL
probe --read-only --ids CONFIRMED_IDS --target TARGET_ID --profile PROFILE --port PORT --out NEW_JSONL
```

프로필 `paired-target-180`, `paired-selected-30`, `bracket-target-60`은 각 20초다. 로그는 새 파일만 생성한다. 분석 시 성공 표본은 `event=read`만 집계한다. `stop`에 보존한 마지막 원문을 중복 집계하지 않는다.

연결 모터 수와 전원 부하·경로가 바뀌므로 결과만으로 특정 부품 불량을 단정하지 않는다. 준비가 통과했다는 사실은 실제 온도 이상이 해결됐다는 뜻이 아니다.
