# 홈·목표 읽기 결과와 피드백 대조

## 운영자가 실제 확인한 결과

`readonly_diagnostics/1793945949525475776/RESULT.json`을 파일로 확인했다. SHA256: `615701df9ac397fef0d0511377872bc28e65e572c28afed2b21521008f56796f`.

- 토크6개OFF, 위치모드6개0, 최고31°C. home은 몸체5개 모두±5도 이내다.
- 현재 원시 위치는 [2034,831,3082,2575,1812,531].
- Goal_Position(주소42) 읽기는 전부0. 각 응답은 정상 길이·ID·체크섬·오류 상태를 통과했다.
- READ54개, 차단0, 모터 쓰기0, 포트 닫힘, 정리 오류0. FIELD_STOP 원본 보존.
- 현재 통신 오류나 home 오류가 이 결과에 나타나지는 않는다. 기존 초기화 뒤 토크 불일치의 직접 원인은 아직 미확인이다.

원래 초기화 이후 DC 전원을 껐다 켰다. 현재 목표0을 근거로 과거 Goal 쓰기가 그 당시에 실패했다고 단정할 수 없다.

## 다음에 읽을 곳

[장치 공급사의 ST 메모리 표](https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation)는42번에 목표 제어값,67번에 읽기 전용 현재 목표 피드백을 별도로 기술한다.42가0이라는 사실만으로67값이나 실제 토크ON 동작을 추정하지 않는다. 해당 장치 펌웨어에서67이 무엇을 반환하는지는 아직 실측하지 않았다.

`--inspect-goal-feedback`은 기존 위치 읽기에67번2바이트 READ만 추가한다. 설치된 SDK의 `_sync_read(addr,length,motor_ids,num_retry=0,raise_on_error=True)`를 사용하고, 기존 엄격한 응답 보호를 거친다. SDK의 제어표 이름이나 설치본을 수정하지 않는다.

- 42·56·67을 서로 다른 필드로 보존한다.
-67 읽기 후 해당 모터의 토크OFF를 다시 검사한다.
- 응답 오류·토크ON이면 추가 읽기 중단. 목표·토크 쓰기0.
-67이 현재 위치와 같더라도 live 허가나 기존 중단 기록 해제는 하지 않는다. 기존 초기화 실행기도 계속 중지 상태다.

## 운영자의 다음 한 줄

DC 전원은 이미 켜져 있으므로 그대로 유지한다. 팔 지지·즉시 전원 차단 가능 상태에서 실행하고 READ_ONLY를 직접 입력한다.

```bash
sudo -v && bash ~/v6_process_1793887709179/operator_candidate/run_torque_readonly.sh --execute --inspect-position-goal --inspect-goal-feedback --operator-present --supported --stop-ready
```

## 소프트웨어 검증

피드백 비교·오류 시 추가 읽기 중단·잘못된 옵션 조합 거부 테스트3개를 추가했다. 로컬과 Jetson 각각 관련83개(68+15) 통과(exit 0), 장치·추론0. Jetson 기본 계획·파일 검사도 통과(exit 0). Wi-Fi 단절 뒤 복구된 연결로 기존 파일을 백업해 배포했다. `goal_feedback_validation/`에 영수증·모의 결과를 보존한다. 실제67번 읽기는 운영자의 다음 실행 전까지 NOT_RUN이다.
