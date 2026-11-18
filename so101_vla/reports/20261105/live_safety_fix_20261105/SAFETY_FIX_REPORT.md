# 안전 처리 보완 및 현재 상태 읽기 — 2026-11-05

## 실제 수행

1. SSH 재연결 후 LeLab 종료 소스를 읽었다. `lelab/server.py:1660~1668`의 shutdown_event는 broadcast thread 정리만 수행하며, 해당 함수에는 모터 쓰기 호출이 없었다. 첫 상태 조회에서 이미 inactive/PID 0이었다. 이후 요청에 따라 `systemctl --user stop lelab-jetson-py310.service`를 실행하고 inactive/PID 0을 확인했다. 이미 종료된 상태를 이번 작업이 종료시켰다고 주장하지 않는다. Astra는 active 그대로다.
2. 실제 Jetson v4를 읽어 새 로컬 사본 `source_v4.py`로 보존했다. 원본 SHA256 `af72d76b992c237211c576796219694503986609277da9b877913b550df6ec56`은 종료 시에도 동일했다. 기존 v4, 기존 venv/LeLab, 모델, 데이터는 수정하지 않았다.
3. 별도 v6와 안전 보조 모듈, 읽기 진단, 모의 테스트를 작성·배포했다. 초기 버전 갱신 전 코드/manifest 5개를 `.bak_179387...` 형식으로 보존했다. 정확한 대응은 `deploy_v2_receipt.json`에 있다.
4. 새 모의 테스트: Windows `python -B -m unittest discover -s reports/live_safety_fix_20261105 -p test_live_safety_fix.py` 25 통과/실패0/오류0/제외0, exit 0(0.242초). Jetson 같은 테스트 25 통과/실패0/오류0/제외0, exit 0(0.198초). 전체 회귀나 실기 제어 검증이 아니다. Python 3.10 문법도 검사했다.
5. Jetson에서 `probe_current_state.py --out current_state.json` 실행, exit 0. LeLab inactive, sudo fuser 비점유 확인 후 READ 패킷만 허용했다. 42개 READ, 목표 패킷0, 차단 패킷0, 카메라 열기0, 정리 오류0. 종료 후 fuser도 비점유였다. 진단 경과 3.670초이며 나머지 개별 작업 시간은 미측정이다.
6. 실제 v6 main을 dry-run 인자로 호출하되 안전 거부 경로까지만 확인했다. `REFUSED_BEFORE_DEVICE_IMPORT`, 새 torch/lerobot/cv2/serial import0. exit 0은 예상된 거부를 테스트한 검사 명령의 성공이며, 정책 dry-run/모터 실행 성공이 아니다.

## 변경 파일과 처리

| 파일 | 처리 |
|---|---|
| so101_first_action_test_safe_v6.py | v4에서 새 파일 생성. 기존 단위/해시/정규화/모델/home/LIVE 입력/온도/Overload/이동 제한을 유지. 초기 토크/온도 검사를 카메라 연결 전에도 수행. 5초/30Hz/0.5도/20도초/첫 목표10도 상한 완화 거부. 누적 clip 초과와 큰 점프 거부 |
| live_safety_runtime.py | 추론 전용 스레드와 제어/serial 소유 스레드 분리, 기다리는 동안 상태 조회. timeout/이상/예외/Ctrl+C에서 늦은 결과 폐기. 포트를 먼저 닫으며 새 Goal/토크 쓰기 없음. 소유 스레드의 명시적 구간에서만 Goal_Position 동기 쓰기 1패킷 허용; 토크/모드/보정/Lock 쓰기 차단 |
| probe_current_state.py | 재무장과 분리된 1회 진단. 모터 쓰기 권한 없음. 토크 하나라도 ON, 온도45°C 이상이면 추가 serial 읽기 중단. 기존 중단 기록을 수정하지 않음 |
| test_live_safety_fix.py | 온도/부하/토크/정지/timeout/예외/Ctrl+C, 소유권, 금지 패킷, 종료·직접 포트 닫기, 기록 보존, 기존 수치 게이트 검사 |
| build_v6.py / source_v4.py | 원본 해시 고정 재생성 근거. 기존 출력 덮어쓰기 거부 |

종료 시의 '유지'는 **마지막 목표와 토크를 변경하지 않는다는 뜻**이다. 현재 위치를 새 Goal로 고정하거나 즉시 물리 정지시키는 기능이 아니다. 통신 장애 때 Goal 쓰기로 OFF 토크가 켜질 위험을 피한다. 물리 전원 차단 수단은 별도로 필요하다.

## 현재 상태 읽기 결과

| 관절 | 토크 | 모드 | 온도°C | 현재 값 | home 차이 | 단위 |
|---|---:|---:|---:|---:|---:|---|
| shoulder_pan | 0 | 0 | 32 | -3.2088 | +0.5345 | degree |
| shoulder_lift | 0 | 0 | 33 | -97.3626 | +0.3901 | degree |
| elbow_flex | 0 | 0 | 31 | 96.6593 | +0.1072 | degree |
| wrist_flex | 0 | 0 | 32 | 43.5604 | +0.1033 | degree |
| wrist_roll | 0 | 0 | 32 | -4.6154 | -2.3634 | degree |
| gripper | 0 | 0 | 35 | 7.1603 | +2.8849 | percent_0_100 |

파일/모터 보정 레지스터 일치. 팔 5축 home±5° 통과. 그리퍼 차이는 도가 아닌 percentage-point다. 이 표는 진단 시점의 측정값이며 이후 상태를 보장하지 않는다. 토크를 켜거나 목표를 전송하지 않았다.

## 중단 기록 조사

- 이번 배포 시작 전 `before.json`부터 원래 경로 `SERIAL_STOP.json`은 없었다. 진단 결과의 `stop_record_present:false`는 **해당 활성 파일 경로만** 뜻한다.
- 보존본 `~/nov_live_prep_20261104/SERIAL_STOP.cleared_20261105_185629.json`을 찾았다. 누가 언제 어떤 절차로 이름을 변경했는지는 미확인이다. 파일명만으로 정당한 재무장이 완료됐다고 판단하지 않는다.
- 보존본 SHA256 `16bf71d5f09ebb246a573a22cbad1faae57e5f50c0ae6ed945f2443c94797f5c`은 조사/최종 검증에서 동일했다. 이 작업은 중단 파일을 삭제/이름 변경/내용 수정하지 않았다.
- v6는 활성 기록뿐 아니라 이름 변경 보존본도 감지해 추가 접근을 거부한다. 파일 이름을 바꾸는 것만으로 자동 재개하지 않는다. 실제 거부 경로를 검사했다. 재무장 기능은 이번에 구현하거나 실행하지 않았다.

## 경로

Jetson 새 작업 폴더:

```text
~/live_safety_fix_20261105_1793874536825/
```

이 폴더의 `current_state.json`, `stop_inventory.json`, `deploy_v2_receipt.json`, `v6_gate_result.json`, `final_verification.json`이 실제 근거다. 코드는 기존 `~/nov_live_prep_20261104`나 사용자 홈의 v4를 덮어쓰지 않았다.

## 남은 미확인 / 미실행

- v6의 실제 카메라/추론/명령 전송 통합은 NOT_RUN. 모의 테스트로 실제 제어가 안전하다고 단정하지 않는다.
- 추론 중 감시는 협력적 소프트웨어 감시다. GIL/SDK/카메라 초기화/운영체제 정지에 독립적인 하드웨어 watchdog이 아니며 30Hz를 보장하지 않는다. 카메라 producer 신선도와 물리 정지 응답, 절대 충돌 한계는 미검증이다.
- 기존 측정의 외부 autocast 없는 추론 약1.98초는 v6의50/30=1.667초 제한보다 느리다. 다음 시도에서도 HOLD될 수 있다. 정밀도 설정을 자동 변경하거나 timeout을 늘리지 않았다.
- 현재 토크6개OFF이므로 live all-ON 조건은 미충족이다. ENABLE/LIVE 입력 대신하기, 토크/Goal/모드 쓰기, 카메라 접근, 추론, 학습, push는 모두 NOT_RUN.
- RobotController 실물 통합/독립 정지 장치 검증/G5/G6 통과를 주장하지 않는다. v6는 별도 첫 동작 시험 후보이며 아직 live 실행 허가를 의미하지 않는다.

## 다음 안전한 명령

현재는 결과만 읽는다. live 명령이나 재무장 대신 실행할 명령이 아니다.

```bash
cat ~/live_safety_fix_20261105_1793874536825/current_state.json
```

이후에는 현장 재무장 절차와 실제 추론·감시 통합을 검증해야 한다. 중단 보존본을 지우거나 기존 v4를 실행해 우회하지 않는다.

## 복구

기존 v4/환경/데이터/모델은 변경하지 않아 복구가 필요 없다. 새 폴더 안에서 수정 전 코드가 필요하면 `deploy_v2_receipt.json`의 파일별 `.bak_...` 대응을 사용한다. 이번 생성 폴더와 기록은 보존한다. 복구를 위해 중단 보존본을 삭제하거나 토크를 변경하지 않는다.
