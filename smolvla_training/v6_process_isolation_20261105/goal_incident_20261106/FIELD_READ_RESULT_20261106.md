# DC 전원 확인 후 읽기 진단 결과

## 실제 확인

운영자가 DC 전원을 켠 뒤 실행한 `readonly_diagnostics/1793945302148373569/RESULT.json`을 SSH로 파일만 읽어 확인했다.

- 두 차례 모두 관절6개 Torque_Enable=0.
- 각 차례 온도: shoulder_pan22, shoulder_lift23, elbow_flex22, wrist_flex22, wrist_roll22, gripper25°C.
- 응답24개, 통신 결과0, ID·길이·오류 상태 정상. 엄격한 체크섬 검사도 통과한 경로다.
- 모터 레지스터 쓰기0, 차단 패킷0, 포트 닫힘, 정리 오류0.
- 결과 SHA256: `f8a77d67df1d11eb717ef64d5a2cfb87ebec34130a28215b9713d263b2a7e952`.
- FIELD_STOP SHA256: `e8a2bf9eb72c676198483aa8b4e150058a4595f37c7b35f6015216798deee56c`, 원래 기록 그대로.

앞선 `1793945043382837853` 진단의 -6은 설치된 `scservo_def.py:23`에서 COMM_RX_TIMEOUT이다. 원시 수신0바이트이고 토크값은 없었다. 운영자는 당시 모터 DC 전원이 꺼져 있었다고 확인했다. 그 결과는 토크ON이나 모터 고장 증거로 사용하지 않는다.

이후 전원이 켜진 읽기 진단에서 현재 통신과 토크OFF를 확인했으나, 과거 Goal 쓰기 뒤의 불일치 원인은 아직 미확인이다. 이번 통과를 live 허가로 사용하지 않는다.

## 다음 읽기 단계

`--inspect-position-goal`을 추가했다. 기존 토크/온도 검사 뒤, 각 관절의 Torque_Enable·온도·Operating_Mode·Present_Position·Goal_Position을 검증된 응답으로 읽는다. 위치 모드0, 토크OFF, 온도45°C 미만을 유지하지 않으면 즉시 추가 읽기를 중단한다.

현재 위치와 목표 원시값의 일치 여부 및 저장된 home과의 차이를 보고할 뿐이다. Goal_Position은 READ만 허용한다. 토크·목표 쓰기, 자동 초기화, 자동 재무장, live는 없다. home 파일은 검토한 SHA256과 일치해야 한다. 목표가0이거나 보정 범위 밖이어도 해당 사실을 보고하며, 임의로 고치지 않는다.

```bash
sudo -v && bash ~/v6_process_1793887709179/operator_candidate/run_torque_readonly.sh --execute --inspect-position-goal --operator-present --supported --stop-ready
```

DC 전원은 이미 켜진 상태로 두고 READ_ONLY를 입력한다. 검사 종료 뒤 마지막 JSON 또는 보고 경로로 다음 단계를 판단한다. 목표 쓰기나 live 명령은 아직 제공하지 않는다.

## 코드 검증

위치/목표 읽기와2바이트 응답에 대한 새 테스트5개, 기존75개를 합해 로컬80개 통과. 첫 Windows 실행은 임시 폴더 권한 때문에3개 환경 오류가 났으며, 작업 폴더 아래 임시 경로를 사용해 모두 통과했다. 안전 조건은 수정하지 않았다. Jetson에서도65개+15개=80개 통과(exit 0), 위치 읽기 옵션의 기본 계획·파일 검사 통과(exit 0). `position_review_validation/`에 영수증·로그를 보존했다. 실제 위치·목표 읽기는 아직 NOT_RUN이다.

**개발자가 NOT_RUN:** 실제 모터·serial·카메라 접근, 추론·학습, 위치/목표 실기 읽기, 목표/토크 쓰기, live, push. 실기 읽기는 운영자가 직접 수행한다.
