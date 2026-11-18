# 토크 준비 수정 결과

후속 실제 실행: 사용자가 dry-run 직접 실행을 승인한 뒤 `smolvla_agent_dry_20261104_234610`을 실행하여 exit0, 1chunk/정책step0/추론3.030253초/최고35°C를 확인했다. 첫 목표 차이 최대는5축0.493137도, 그리퍼0.059521퍼센트포인트로6개모두1단위 이내였다. 시작 이미지3장640×480 RGB를 직접 열어 확인했다. 사선 카메라 초기 read failed3회 경고는 있었으나 연결·프레임 비교·추론이 완료됐다. 정책 live는 계속 NOT_RUN이다. 상세는 `../dryrun_result_20261104_234610/DRY_RUN_RESULT.md`를 따른다. 아래 NOT_RUN 서술은 토크 준비 완료 당시 기록이다.

## 확인된 발생 조건

직접 SSH 진단에서 OFF 뒤 6개 모두0을 확인한 후, 현재 위치와 같은 Goal_Position만 한 번 썼다. 약3밀리초 뒤 토크 표본은 shoulder_pan=0, 나머지5개=1이었다. 명시적 Torque_Enable=1 패킷은 없었다. 다음 검사에서는 6개 모두1이었다.

확인된 것은 목표 위치 쓰기 직후 재활성화가 관측되는 구간이다. 보드·모터 펌웨어 내부의 정확한 이유는 UNVERIFIED이다. SDK 소스에서 별도의 숨은 ON 쓰기는 확인되지 않았다. 위치 SYNC_READ의 Incorrect status packet 오류도 별도로 관측했으며, 일괄 읽기를 개별 READ로 바꿨다.

## 적용한 변경

최신 도구는 `~/torque_so101_verified_goal_v3.py`이다. 기존 파일과 LeLab·SDK·데이터·모델은 보존했다.

- 위치/목표는 관절별 READ, 재시도0으로 읽는다.
- 시작 토크6개OFF, 위치 모드0, HOME±5도, 시작온도50°C미만, 부하/Status, 단위·보정·모델 해시 검사를 유지한다.
- 기존 Goal_Position과 Present_Position의 원시 값이 두 번 모두 정확히 일치해야 한다. 준비 중 위치 변화도 거부한다.
- Goal_Position 쓰기는 금지한다. 허용된 실제 쓰기는 명시적 토크ON 한 번뿐이다.
- 기존 목표가 현재 위치와 다르면 자동 수정하지 않고 거부한다. 이 상황까지 자동 복구하는 일반 시작 도구가 아니다.
- 이미ON 또는 일부ON이면 enable은 그대로 거부한다. 해제는 지지 확인을 거친 별도 disable 경로다.
- 종료는 토크를 끄지 않고 포트를 닫는다. 종료 뒤 상시 온도 감시는 없다.

## 실제 실행 결과

관련 mock 테스트109개 통과, 실패0/오류0/제외0, exit0. 새 검증본 테스트는10개이다. Jetson에서 직접 새 파일 생성, 해시 대조, Python3.10 문법 확인 후 실행했다.

현장 결과: `TORQUE_ON_HOLD`, exit0. 토크6개 모두1, 위치 모드6개 모두0, 최고35°C. 기존 목표와 현재 위치 원시 값은 `[2065,847,3083,2575,1810,530]`으로 일치했다. 목표 쓰기0회, 명시적 토크ON1회였다. 시작과 종료의 읽힌 위치는 같았다. 물리적 미세 움직임이나 정책 성공을 검증한 결과는 아니다.

기록:

- 원인 분리: `~/torque_goal_probe_20261104_233427/events.jsonl`
- OFF 확인: `~/torque_guard_20261104_233859/events.jsonl`
- 성공한 준비: `~/torque_guard_20261104_233913/events.jsonl`

마지막 확인 상태는 토크ON이다. 손으로 팔을 움직이지 않는다. 정책 dry-run/live와 새 카메라 열기·추론·학습은 이번 작업에서 NOT_RUN이며 G5/G6 통과로 기록하지 않는다. push도 하지 않았다.

## 다음 작업과 안전한 재개

다음 작업은 근거 파일을 포함한 정책 dry-run1회와 이미지/첫 목표 차이 검토다. 아직 실행하지 않았다. 기존 v4 정책 경로에는 SYNC_READ가 남아 있으므로 이번 토크 준비 성공을 정책 제어 통신 검증으로 대체하지 않는다.

장치 없이 성공 기록만 다시 읽는 명령:

```bash
python3 -B -c 'import json,pathlib; p=pathlib.Path.home()/"torque_guard_20261104_233913/events.jsonl"; print(*[json.loads(s) for s in p.read_text().splitlines() if json.loads(s).get("event")=="RESULT"],sep="\n")'
```

정상 종료 후 토크를 끄려면 팔/물체를 안정되게 지지하고 다른 serial 사용이 없는 상태에서 별도 실행한다. 이 문서의 명령은 자동 실행하지 않는다.

```bash
source ~/nov_env.sh && bash ~/nov_preflight_reviewed.sh && ~/venvs/smolvla-py310/bin/python -B ~/torque_so101_verified_goal_v3.py --disable --confirm-supported --execute
```

`SUPPORTED` 확인이 필요하다. 6개0과 `TORQUE_OFF_SUPPORTED`를 확인한다. 상태가 다르면 중단하며 반복 OFF/ON으로 우회하지 않는다.

롤백: 새 도구는 별도 파일이므로 원본 복원이 필요하지 않다. 기존 목표 쓰기 방식은 재활성화 구간이 관측돼 안전한 실행 대안으로 사용하지 않는다. 기존 도구는 분석용으로 보존한다.
