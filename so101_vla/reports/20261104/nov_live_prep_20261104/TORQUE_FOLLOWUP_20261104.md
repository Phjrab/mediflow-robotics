# 토크 재활성화 후속 확인

## 사용자 실행에서 확인된 결과

사용자가 제공한 `torque_transition_20261104_223540/events.jsonl` 출력 기준이다. 이 PC에서 Jetson 로그 원본을 열거나 진단을 실행하지 않았다.

| 단계 | 6개 관절 토크 |
|---|---|
| 최초 연결·보정 확인 | 모두 1 |
| 명시적 OFF 한 번 직후 | 모두 0 |
| 동일 연결에서 네 번 확인 | 모두 0 |
| 포트 닫기·재연결 직후 | 모두 0 |
| 재연결 후 보정 확인 | 모두 0 |

결과는 `NOT_REPRODUCED_IN_ONE_PROCESS`, 개별 읽기 패킷 252, OFF 패킷 1, 차단 0이다. 최고 온도 35°C, 보고된 위치 변화 없음. 이 실행 범위에서는 포트 재연결이나 보정 조회가 토크를 재활성화하지 않았다. 다른 실행·다른 프로세스·다른 읽기 경로까지 배제하는 결과는 아니다.

## 찾은 코드 문제와 수정

기존 `torque_so101.py`의 `diagnostics()`는 토크를 먼저 읽고 `sync_read("Present_Position")`와 온도 읽기를 수행했다. 출력·성공 판정은 그 이전 토크값을 사용했다. 따라서 뒤의 조회 중 상태가 바뀌면 OFF 성공을 잘못 보고할 수 있다. 실제로 상태가 그 구간에서 바뀌었다는 증거는 아직 없다.

기존 파일을 보존하고 `torque_so101_fresh.py`를 만들었다. 위치·온도·모드 조회가 끝난 뒤 토크를 새로 읽는다. OFF 후 0.25초 뒤에도 다시 확인하며, 활성화 직전 마지막 원시 위치 조회 뒤에도 OFF를 재확인한다. home·보정·온도·모드·현재 위치 목표 우선 기록·사용자 확인 조건은 유지한다. 이미 ON이면 자동 해제하거나 성공으로 처리하지 않는다.

이 수정은 오래된 토크값으로 성공을 판정하는 문제를 막는다. 토크 재활성화 원인 자체를 해결했다고 판정하지 않는다. 수정본은 로컬에만 있으며, 현장 교체·실행은 NOT_RUN이다. 원인 분리 전 기존 enable 또는 live를 반복하지 않는다.

## 다음 비교 진단

첫 진단은 개별 `read()`만 사용했다. 기존 도구는 위치에 `sync_read()`를 사용한다. `torque_transition_probe_v2.py`는 같은 개별 읽기·OFF·재연결 검사 후, `--compare-sync-read`를 지정했을 때만 기존 위치 SYNC_READ를 한 번 비교한다. 토크는 위치 조회 뒤의 최신값으로 판단한다.

송신 가드는 기존 규칙에 더해 비교 단계의 Present_Position(56, 2), 모터 ID 1~6 SYNC_READ(130) 한 번만 허용한다. 토크 ON·목표 위치·다른 쓰기·추가 SYNC_READ는 전송 전에 차단한다. 현재 설치본의 상수와 레지스터가 확인값과 다르면 포트를 열지 않는다. 이 가드는 이 Python 프로세스에만 유효하며 외부 프로세스·하드웨어 동작을 막는 장치는 아니다. 팔과 물체는 계속 안정되게 지지해야 한다.

패킷 형식의 공개 근거는 제조사의 [GroupSyncRead](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/group_sync_read.py)와 [syncReadTx](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/protocol_packet_handler.py)이다. 공개 소스는 실제 Jetson 설치본과 같다는 증명이 아니므로 런타임 형식 검사를 유지한다.

| 결과 | 해석 |
|---|---|
| `BLOCKED_WRITE` | 금지된 송신 시도를 차단했다. 단계·호출 스택으로 해당 경로를 수정한다. |
| `TORQUE_CHANGED_IN_SYNC_READ_WINDOW` | 위치 SYNC_READ 전후 구간에서 변화했다. SDK가 ON을 보냈다는 뜻은 아니며, 패킷 기록과 함께 원인을 좁힌다. |
| `TORQUE_CHANGED_AFTER_SYNC_READ_DELAY` | 비교 직후 지연 구간에서 변화했다. 변화 주체는 UNVERIFIED. |
| `SYNC_READ_COMPARISON_NOT_REPRODUCED` | 이 비교에서도 변화가 없었다. 별도 프로세스 경계·간헐적 동작 등은 여전히 UNVERIFIED. |
| 예외·통신 실패·다른 결과 | 중단한다. 기존 게이트를 해제하거나 live로 넘어가지 않는다. |

## 실행·미실행

- 신규 16개 mock 테스트가 전체 실행에 포함되어 통과했다. 장치·추론 없음.
- `python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_*.py'`: 기존 51개와 신규 16개, 총 67개 통과, 실패 0, 오류 0, 제외 0, exit 0.
- 두 새 스크립트의 기본 PLAN_ONLY 실행은 exit 0, Python 3.10 문법 검사도 통과했다.
- Jetson 접속·복사·장치·토크 변경·카메라·추론·학습·push는 이 PC에서 모두 NOT_RUN.
- 복귀: 새 파일은 기존 파일을 덮어쓰지 않는다. 기존 파일로 돌아갈 수 있으나 기존 OFF 판정의 시점 문제는 남아 있으므로 live 실행을 재개하는 근거로 쓰지 않는다.

## 사용자 실행 순서

### 직접 실행에서 발생 조건 재현 및 토크 준비 성공

사용자가 SSH 직접 실행을 승인한 뒤, seq v2 enable을 직접 실행했다. 최초6개 토크가 모두1로 읽혀 모터 쓰기 없이 거부됐고 exit1이었다. SDK의 GroupSyncWrite.makeParam/txPacket 및 syncWriteTxOnly/txPacket 소스를 Jetson에서 직접 읽었다. 확인된 SDK 경로에는 별도 숨은 토크ON 쓰기가 없었고 송신은 가드를 통과하는 writePort를 사용했다.

새 `torque_goal_transition.py`를 작성하고 mock7개 및 문법 검사를 통과한 뒤 SSH로 새 파일에 직접 저장했다. 해시 일치와 문법도 확인했다. 이 도구는 HOME/온도/모드/보정/단위/모델 근거 검사를 유지하면서 OFF1회와 현재 위치 목표1회만 허용하며 명시적 ON은 허가하지 않는다.

실제 원인 분리 결과 `REENABLED_AFTER_CURRENT_GOAL_WRITE`, exit0. OFF 직후와0.25초후6개모두0, 현재 위치 목표 쓰기 약3밀리초 뒤 shoulder_pan=0/나머지5개=1이었다. 허가된 쓰기는 OFF와Goal의2회, 명시적 토크ON0회였다. 로그는 `~/torque_goal_probe_20261104_233427/events.jsonl`이다. 확인된 발생 조건은 목표 쓰기 직후 토크 재활성화이며, 보드·펌웨어 내부 원인 자체는 UNVERIFIED이다.

수정본 `torque_so101_verified_goal_v3.py`는 목표 쓰기 자체를 금지한다. 기존 목표와 현재 원시 위치를 두 번 대조하고, 모두 정확히 같을 때만 나머지 안전 조건과 최신 토크OFF를 재확인한 뒤 명시적 ON을 한 번 보낸다. 기존 목표 불일치이면 자동 수정하지 않고 거부한다. 이미ON 게이트를 완화하거나 일부ON을 성공으로 처리하지 않는다. 이 도구가 모든 시작 상태를 자동 준비하는 것은 아니다.

직접 SSH로 v3를 새 파일로 저장하고 Python3.10 문법과 SHA256 `6d398d3306498699cb45975033f9e03c173edaf088bc9d63d28bbdf64ac2d3fe`를 확인했다. HOME 지지 상태를 확인한 승인 범위에서 별도 OFF를 실행해6개0을 확인했고, 새 v3를 실행했다. `TORQUE_ON_HOLD`, exit0, 토크6개1/모드6개0, 최고35°C. 기존 목표와 현재 위치 원시 값은 `[2065,847,3083,2575,1810,530]`으로 일치했다. 목표 쓰기0/명시적ON1, 전후 측정 위치 동일이었다. 정상 종료는 토크유지 포트닫기다. 마지막 확인 상태는6개ON이다.

관련 로컬 테스트109개 통과, 실패0/오류0/제외0, exit0. 기존99개에 v3검사10개 추가다. 성공 로그 `~/torque_guard_20261104_233913/events.jsonl`, OFF 로그 `~/torque_guard_20261104_233859/events.jsonl`. 새 정책 dry-run/카메라 열기/추론/학습/live/push는 NOT_RUN. RobotController 통합이나 G5/G6 통과가 아니며, v4의 SYNC_READ 통신은 이후 별도 검증 대상이다.

### 보강본 사용자 실행에서 새로 확인된 통신 실패

사용자 출력에서 최초 토크 6개 모두0, 위치 모드 모두0, 최고35°C이고 HOME 범위 안이었다. 원시 위치 조회 직후에도 토크가 모두0이었다. 현재 위치 목표 쓰기의 허가·송신 완료가 기록됐고, 목표 레지스터 읽기 대조를 통과한 다음 `after_current_goal` 상태 확인의 Present_Position SYNC_READ에서 `Incorrect status packet`으로 중단했다.

토크ON 패킷의 허가/송신 기록은 없으며 코드상 해당 지점에도 도달하지 않았다. 따라서 이 실행에서 명시적 토크ON을 보냈다고 기록하지 않는다. 다만 오류 발생 뒤 실제 Torque_Enable 값은 조회되지 않았으므로 종료 뒤 모두OFF라고 단정하지 않는다. 이전 재활성화 현상과 같은 원인인지도 UNVERIFIED이다. 이번 실행이 막힌 직접 원인은 SYNC_READ 응답 오류이며, 혼선·타이밍·SDK·펌웨어·USB 중 어느 것이 근본 원인인지는 미확인이다.

기존 보강본을 보존하고 `torque_so101_guarded_read_v2.py`를 추가했다. 위치와 목표 레지스터의 모든 `sync_read`를 동일한 공개 API인 `read(register, joint, normalize=..., num_retry=0)`의 관절별 조회로 바꿨다. 원시 위치에는 normalize=False, HOME/관절 비교에는 normalize=True를 유지한다. 송신 가드에서도 SYNC_READ를 차단한다. 쓰기 재시도·추가 쓰기·통신 오류 무시는 추가하지 않았다.

현재 위치 목표 기록→목표 읽기 대조→HOME/상태 재확인→0.5단위 이내 안정성 및 원시 위치 동일 확인→최신 토크OFF→명시적 ON의 순서는 동일하다. 개별 읽기는 동시 표본이 아니므로 준비 도중 위치 변화가 있으면 기존 재검사로 거부한다. 통신 실패 시에도 동일하게 중단하며 자동OFF/ON으로 우회하지 않는다.

새 mock 테스트는 9개이며 정상 동작·개별 조회 오류·목표 불일치·준비 중ON·시작 온도/모드/HOME/NaN·원시 위치 변화·SYNC_READ 차단·패킷 오류·관절 순서/정규화 보존을 확인한다. Jetson 전송/실제 실행 전까지 현장 개선 여부는 NOT_RUN/UNVERIFIED이다. 기존 패키지·기존 Jetson 파일·정책 live 코드는 수정하지 않는다.

실제 로컬 검사: `python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_*.py'` 총92개 통과, 실패0/오류0/제외0, exit0. 수정본 기본 PLAN_ONLY exit0, Python3.10 문법 검사 통과, sync_read 호출0 확인. 수정본 SHA256 `4485462a01d2bd8658e28313190dae48ab42f3a8ff100d977c62a7344afd9a3d`. 이 PC에서는 장치·추론·학습·Jetson 접속·복사·push를 실행하지 않았다.

### 원격 접속 직접 확인 — 위 NOT_RUN 기록 이후

사용자가 직접 SSH로 코드 전송·오류 확인을 할 수 있는지 다시 요청했다. 로컬 제한 환경의 연결은 소켓 단계에서 Permission denied였으나, 승인 검사를 거친 제한 밖 연결은 SSH 서버에 도달했다. 비대화식 인증 실패를 비밀번호 오류로 해석하지 않았다. 이어 대화형 SSH 암호 프롬프트로 인증하여 `SSH_READONLY_OK`를 확인했고 exit0으로 종료했다. 암호를 명령줄 인자나 파일에 저장하지 않았다. 기존 사용자의 SSH 창을 제어한 것이 아니라 별도 연결이었다.

별도 읽기 전용 SSH 명령으로 전송된 `torque_so101_guarded_read_v2.py`를 읽어 SHA256이 `4485462a01d2bd8658e28313190dae48ab42f3a8ff100d977c62a7344afd9a3d`와 일치함을 확인했다. Jetson의 표준 라이브러리 ast로 Python3.10 문법 검사를 수행하여 `PY310_SYNTAX_PASS`, exit0을 확인했다. 마지막 송신 로그에서도 앞서 수신한 동일한 Present_Position SYNC_READ 응답 오류를 읽었다.

이후 SSH 접속과 파일 읽기·문법 검사는 ACTUAL_EXECUTED로 정정한다. 코드 복사/변경은 이 연결에서 하지 않았고, 로봇·모터·serial·카메라 열기, 토크 활성화, 추론·학습·live·push는 계속 NOT_RUN이다. 실제 장치 실행은 사용자가 직접 한다는 범위를 유지한다. 주소·계정·호스트명·암호는 이 기록에 남기지 않는다.

### v2 사용자 실행 결과 수신

사용자가 두 진단의 출력을 함께 첨부했다. 새 실행의 결과는 `SYNC_READ_COMPARISON_NOT_REPRODUCED`, 읽기 패킷 331, OFF 패킷 1, 차단 0, 위치 SYNC_READ 1이다. 시작부터 종료 전 마지막 표본까지 6개 토크가 모두 0이었다. SYNC_READ 전후와 0.25초 지연 후에도 0이며 위치는 동일하고 최고 온도는 35°C였다.

따라서 위치 SYNC_READ가 재활성화를 유발한다는 가설도 이번 실행에서는 뒷받침되지 않았다. 샘플링 순서 개선은 유효하지만 실제 재활성화 원인 해결로 해석하지 않는다. 이전 실행과 이번 실행은 프로세스가 달라도 OFF가 유지됐으므로 단순한 프로세스 전환만을 원인으로 단정할 수도 없다.

다음은 실패가 발생했던 실제 `torque_so101_reviewed.py`와 이 도구가 불러오는 v4 파일을 로컬 검토본 해시와 대조하는 읽기 전용 확인이다. helper의 기대 해시는 `c32aa428492324c70cd98c06c1470f0b50bec4ebe1abfcaa7389c4ea9418a48e`, v4의 기대 해시는 `af72d76b992c237211c576796219694503986609277da9b877913b550df6ec56`이다. Jetson 실제 해시는 출력 수신 전까지 UNVERIFIED. 파일이 같다면 자동 토크ON을 가정하거나 기존 gate를 우회하지 않고 실패한 호출 경로 자체를 추적해야 한다.

현재 원인은 UNVERIFIED이며 live는 NOT_RUN이다. 사용자 실행 결과를 읽어 분석했으며 이 PC에서 Jetson·serial에 접속하거나 패키지를 수정하지 않았다.

### 실제 실행 파일 해시 대조와 보강본

사용자가 출력한 helper와 v4 해시가 위 두 기대값과 각각 정확히 일치했다. 실제 파일 버전 차이는 배제됐으며, 토크 재활성화 원인은 여전히 UNVERIFIED이다. 반복 진단의 음성 결과를 원인 해결이나 live 허용으로 기록하지 않는다.

기존 파일을 보존하고 `torque_so101_guarded.py`를 추가했다. v4 해시·단위·체크포인트·보정 근거, home, 온도, 부하/Status, 위치 모드, 시작 토크OFF 조건을 유지한다. 위치·온도·모드 조회 뒤 토크를 새로 확인한다. 준비 도중 원시 위치가 달라지거나 토크가 이미ON이면 중단하며 자동OFF→ON으로 우회하지 않는다.

프로세스의 SDK `writePort` 앞에서 개별 READ와 확인된 위치/목표 SYNC_READ 외의 패킷은 차단한다. enable에서 예외적으로 허용하는 쓰기는 현재 원시 위치와 동일한 Goal_Position 전체 패킷 한 번, 목표 읽기 대조·두 번째 상태 확인·최신 토크OFF 확인 후 명시적 Torque_Enable=1 전체 패킷 한 번뿐이다. disable은 운영자 지지 확인 후 Torque_Enable=0 전체 패킷 한 번만 허용한다. 재시도·다른 값·다른 레지스터·토크 쓰기 선행·다른 포트 송신은 거부한다. 각 쓰기의 허가와 전송 완료는 구분하여 로그에 남긴다.

버스를 연 직후 TIOCEXCL을 적용한 뒤 보정 레지스터를 읽는다. 종료는 disconnect(False)로 토크 상태를 유지한다. SDK 종료 실패 시 가드를 해제하기 전에 원시 포트를 닫는다. 예외 시 자동 토크 해제는 하지 않는다. 다른 프로세스·직접 serial 쓰기·펌웨어·전원 동작까지 전역 차단하는 장치는 아니며 외부 재활성화 원인 해결을 보증하지 않는다.

출력은 새 `torque_guard_<시각>/events.jsonl`이며 기존 경로가 있으면 거부한다. 단계별 토크 표본, 허가·전송 쓰기, 금지 명령과 호출 스택, 오류를 기록한다. LeLab·SDK 패키지·기존 Jetson 파일은 수정하지 않는다. 별도 새 파일로 전달할 예정이며, 전송과 실제 활성화는 사용자 실행 단계다.

- 새 가드 mock 테스트 16개, 이전 관련 테스트 67개를 합해 `python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_*.py'` 총 83개 통과, 실패0/오류0/제외0, exit0.
- 보강본 `--enable` 기본 PLAN_ONLY 실행 exit0. Python3.10 문법 검사 통과.
- 보강본 SHA256: `50b4985cebcad11d97838e792f1df1cfff347b23f4d608c7ce8cfb375eaa09e1`.
- Jetson 접속·복사·장치 실행·토크 변경·추론·학습·push는 이 PC에서 NOT_RUN. 사용자에게 다음 단계로 새 파일 전송 명령 하나만 제공한다.

한 단계씩 안내한다. 먼저 새 v2 진단 파일만 전송하고, 전송 성공 후 별도 명령으로 실행한다. 실행 명령은 `source ~/nov_env.sh` 후 `bash ~/nov_preflight_reviewed.sh && ~/venvs/smolvla-py310/bin/python -B ~/torque_transition_probe_20261104_v2.py --execute --confirm-supported --compare-sync-read`이다. 실행 중 SUPPORTED 확인이 필요하다. 이 명령은 사용자 실행용이며 여기서 실행하지 않았다.
