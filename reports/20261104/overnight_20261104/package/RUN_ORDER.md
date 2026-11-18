# 무인 작업 결과를 반영한 실행 순서

## 현재는 serial 중단 상태

최초 안전 읽기에서 shoulder_pan의 Torque_Enable=1을 관측했다. 즉시 중단해 나머지5개 토크·온도·위치·모드를 읽지 않았다. 이 값들은 UNVERIFIED이며 이전 저녁 결과를 현재 값으로 대체하지 않는다. 포트는 닫았고 OS fuser 결과1/출력없음으로 비점유를 확인했다.

`SERIAL_STOP.json`이 있으면 모든 후속 serial 진입과 실시간 상태를 요구하는 dry wrapper가 연결 전에 거부한다. 환경변수·인자로 이 기록을 무시하거나 자동 삭제하는 기능은 없다. 토크OFF도 쓰지 않았다. 최종 재읽기도 이 중단 조건 때문에 실행하지 않는다.

현장에서 먼저 저장 기록만 읽는다. 이 명령은 장치를 열지 않는다.

```bash
cd ~/nov_live_prep_20261104 && python3 -B -m json.tool SERIAL_STOP.json
```

전역 중단 해제나 새 실기 읽기는 운영자가 로봇 앞에서 전원/지지 상태를 확인하고 별도로 지시한 뒤에만 설계한다. 여기에는 자동 중단 해제·강제 토크OFF·ON 명령을 넣지 않았다.

## ④ 서비스·점유 확인

```bash
sudo -v && bash ~/nov_live_prep_20261104/nov_preflight.sh
```

- 기대: 현재는 `PORT_FREE` 뒤 `PREFLIGHT_BLOCKED: serial 전역 중단 기록 존재`가 정상이다.
- PID·권한 오류·조회 오류도 거부한다. 오류를 비점유로 취급하지 않는다.
- Astra 브리지는 그대로 유지하며 LeLab/다른 serial 소유자를 자동 종료하지 않는다.

## ④-1 전원 재투입 → HOME 웹 → Ctrl+C

이 단계는 **현장 운영자 전용이며 현재 중단 상태에서는 실행하지 않는다.** 전원 재투입이 토크OFF를 보증하지 않는다. 실제 읽기에서6개 모두OFF·모두45°C미만을 확인할 수 있어야 한다. 이번 무인 작업은 최초1개ON을 읽어 여기에서 중단됐다.

현장 입회 후 새로운 읽기 승인과 중단 상태 처리 절차가 완료된 경우에만 다음 명령을 검토한다.

```bash
source ~/nov_env.sh && ~/venvs/smolvla-py310/bin/python -B ~/nov_live_prep_20261104/home_align_web.py --host 0.0.0.0 --http-port 8765 --max-seconds 120
```

- 기대: `HOME_MONITOR_READONLY_READY rows=6`, 여섯토크OFF,45°C미만,0.25초 주기 갱신.
- 몸체5축은 degree, 그리퍼는0~100 퍼센트포인트다. ±3초록/3초과~5노랑/5초과빨강.
- 토크1/45°C이상/읽기오류/갱신지연이면 진행 중단. ON인 팔을 손으로 무리하게 돌리지 않는다.
- Ctrl+C 또는 최대시간 후 `HOME_MONITOR_CLOSED` 확인. 포트를 닫으며 토크를 바꾸지 않는다.
- 그 뒤 preflight를 다시 실행한다. 모니터 PID가 포트를 소유하면 다음 단계가 차단돼야 한다. 이번 실제 follower 점유 시험은 안전 중단으로 BLOCKED이며 모의 판정 테스트만 통과했다.

## ⑤ 토크 도구 상태/거부 확인

```bash
source ~/nov_env.sh && ~/venvs/smolvla-py310/bin/python -B ~/nov_live_prep_20261104/torque_so101.py --enable --execute
```

현재는 중단 기록으로 연결 전에 거부해야 한다. 정상 상태로 재승인된 경우에도 관절별 토크/위치/home차이/모드/온도를 먼저 출력하고 포트를 닫은 뒤 확인 단계로 간다. 무인 프로파일은 어떤 입력에도 쓰지 않으며 ENABLE/LIVE를 대신 입력하지 않는다. disable의 일부ON 해제 로직은 가짜 버스로만 시험했다. 실제 disable은 실행하지 않았다.

## ⑥ dry-run

```bash
bash ~/nov_live_prep_20261104/nov_policy_run.sh dry
```

현재 공유 중단 때문에 실행 전 거부가 정상이다. `DRY_REVIEW_NUMERIC_PASS`를 얻었다고 보고하지 않는다. 카메라만의 수집과 데이터셋 프레임을 사용한 오프라인 추론은 이 serial 중단과 분리해 수행한다. 실시간 관절값과 결합한 예측은 BLOCKED이다.

## ⑦ live

이번 작업에서는 실행 금지다. 모델 출력 모양·오프라인 오차·mock PASS·실기 성공은 서로 다른 기준이다. 내일 체크리스트의 현장 입회와 별도 live 승인이 필요하다. 기존 안전 검사나 정지 확인을 생략하는 명령은 제공하지 않는다.
