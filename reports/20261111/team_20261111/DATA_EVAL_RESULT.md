# 독립 검증 결과 — 초기 기준 재현

## Production criteria 독립 검증 PASS

총괄이 Jetson 실제 calibration/home/direct_live.py에서 생성해 전달한 `reports/team_20261111/jetson-start-criteria.json`을 읽기만 하여 검증했다. SHA256 `acb49870ee0bf36dc8bbd9099612d74ddd8cadb0a4e20e5b10b0d06c5f4d520e`.

`python reports/team_20261111/evaluation/production_check.py` PASS: 실제 JSON 기반 Python/JS 경계 27사례(각 축 low−1/low/high/high+1 24개와 팔꿈치 3082/3083/3084 3개) 승인 여부 모두 일치. 3082/3083 허용, 3084 차단. 실제 기준 JSON을 JS validateCriteria가 유효 판정했고, Python start_criteria로 재구성한 6축 기준과 전체 일치했다. 검사 전후 기준 JSON/JS/Python 지문 안정. 최초 harness JS 괄호 오타는 실행 전 syntax error로 중단되었고 백업 후 수정·재실행하여 아래 결과를 생성했다.

| 축 | 명령 범위 tick | 시작 범위 tick |
|---|---|---|
| shoulder_pan | 755–3448 | 755–3448 |
| shoulder_lift | 796–3113 | 796–3113 |
| elbow_flex | 880–3082 | 880–3083 |
| wrist_flex | 936–3225 | 936–3225 |
| wrist_roll | 86–3643 | 86–3643 |
| gripper | 413–2047 | 413–2047 |

6축 순서와 home 벡터는 기존 화면 값과 정확히 일치. elbow home `96.552176371108`, gripper home `4.275478211632611`; 몸 5축 degree, gripper percent_0_100(화면 점), tolerance는 Python 공통상수와 동일한 2, alignment_is_full_readiness=false 확인. 상세 결과 `evaluation/production_results.json`, 검사 코드 `evaluation/production_check.py`.

검증 한계: 전달된 기준 JSON 내부 일관성 및 판정 경계 검증이다. source hashes 형식 및 기록은 확인했으나 Jetson 원본 파일 재독출/SSH/장치 동작은 독립 담당이 수행하지 않았다. 실제 웹 읽기 점검 및 배포 파일 지문 확인은 총괄 범위다. 기존 74 synthetic 검사는 재실행하지 않았다.

## 최신 최종본 wire_v3: 로컬 모의 PASS

reviver `context.source` 기반 최종 JSON 해석 및 HOME_TOLERANCE 공통원천 변경 후 다시 검사했다. `evaluation/wire_v3/cross_runtime_results.json`: 74사례 승인 불일치 0, 전후 4개 source hash 안정. UI 3개 그룹 PASS, 실행기 6 tests OK 재확인. `node reports/team_20261111/evaluation/reviver_compat.test.cjs` 추가 PASS: context.source 미지원 시 null(미확인), 중복키 마지막값이 float이면 차단/마지막값 int이면 허용, unsafe integer 차단. 중복키의 마지막값 의미는 Python json.loads와 동일하다. context.source 지원 여부에 따라 구형 실제 브라우저는 항상 미확인일 수 있으므로 총괄의 실제 브라우저 읽기 점검에 포함해야 한다.

최신 검증 지문(아래 이전 최종본 지문은 이력):

- live_start.py: `2b4760a9847fcf37cbb6557f14e9cb3e0e4cb9258b33cb4d64519b2a54d0d466` (실행기 담당 최종 통보)
- start_position_rules.py: `0b54c4a7850b038cf6c4a974d23cab351abb9fd7e7fad152541efffcea15a7fa`
- export_start_criteria.py: `873c65982e171ba36aab99d28b1dd95c056c82fe7a447a436ad49be6bf49558a`
- ui/start-position-ui.js: `4ac0d8e295ae298e9304a5a0926590756d4d585f30182cd1d1871e90e5d519a7`
- ui/home-joints.html: `95023fe443bd6d1cacf4a6bb58c2becac81b7746ce7b9458049caf4af0cfee43`

현장 actual criteria/배포/브라우저 검증은 아직 수행하지 않았으며 총괄 범위다. synthetic 입력 한정 로컬 위치 판정 PASS이다.

## 최종 상태: 로컬 모의 위치 판정 교차검증 PASS

최종 UI 통보 후 실제 JSON 문자열 → `decodeTicks` → `evaluate` 경로로 재검증했다. **74개 사례, 승인 여부 불일치 0개**, 검사 전후 소스 지문 안정. 앞서 발견한 6축 정수값 float 불일치는 해결됐다. 이전 절의 PASS 보류는 이전 지문에 관한 이력이다. 최신 결과는 `evaluation/wire_v2/cross_runtime_results.json`이며 이전 결과를 덮어쓰지 않았다.

추가 독립 실행 결과: `python tmp/vla_direct_live_v3_20261109/tests_start_position_rules.py` 6 tests OK. `node reports/team_20261111/ui/start-position-ui.test.cjs` 3개 검사그룹 PASS: DOM 3082/3083/3084·6축·stale 1200ms·연결종료, wire 소수/지수/bool/null/문자열/중복키 차단, invalid 메시지 직후 이전 OK 즉시 폐기. missing tick은 시작 미확인. Python의 차단과 UI의 미확인은 상태 문구 차이가 있으나 둘 다 승인하지 않는다는 기준으로 비교했다.

최종 SHA256:

| 파일 | SHA256 |
|---|---|
| live_start.py | d1791f908328f97ee1b385e25e7fadaa7788b73a81069a9de51a5438018f8e6b |
| start_position_rules.py | 5c20c0aa7eecfd9398a752ad72e5f459e7b30575c38dbdc9c46b68421116e58b |
| export_start_criteria.py | b145a58a56b90057acce15e96c0741f6d4d570969f01c9c467ff2e78e83e925a |
| ui/start-position-ui.js | a5f10e2fecac9cedf24586ed8a754cb06cf37cf18a03be464ae98a657c8a2f31 |
| ui/home-joints.html | b4d7a703016dac0e395d8e078f9386b595446e05611b4e51a938610a70c196c3 |

남은 문제/총괄요청: 현장 calibration/home/base로 exporter가 생성한 실제 JSON과 배포 파일 지문 확인 및 실제 웹의 읽기 전용 점검은 총괄 범위다. 이번 PASS는 synthetic 보정으로 로컬 실행한 위치 판정·전송 처리에 한정되며, 현장 전체 준비완료/온도/토크/서비스/모델/로봇 동작 승인이 아니다. 원본 범위·목표상한·안전조건은 독립 검증 담당이 변경하지 않았다.

## 수정본 실제 runtime 교차검증 추가 — 통합 PASS 보류

`python reports/team_20261111/evaluation/cross_runtime.py` 완료: 실제 `start_position_rules.validate_position`, 실제 `export_start_criteria.export_criteria`, 실제 UI JS `evaluate`를 사용한 74개 사례 중 6개 불일치. 나머지는 정수 상하한/상하한 초과/문자열/bool/null/비정수/누락틱 및 stale·기준없음 맥락 사례다. 입력은 명시적 synthetic 6축 보정이며 현장 보정값 검증이 아니다. 검사 전후 4개 수정본 SHA256이 같음을 확인했다. 상세는 `evaluation/cross_runtime_results.json`.

불일치는 6축 각각 `float(low)` 한 개씩이다. Python `type(value) is int`는 JSON `100.0`을 거부하고, JavaScript `JSON.parse` 이후 `Number.isInteger(100.0)`는 true여서 UI가 허용한다. 3083.0도 같은 원리다. 소수부분이 있는 3083.1과 다른 오류형은 양쪽 승인하지 않는다. float 정수표기의 타입 손실을 처리하거나 엄격한 전송 계약 검증 근거를 확보하기 전에는 정확한 runtime 등가성을 PASS로 기록할 수 없다. 기존 안전 판정을 완화하면 안 된다. 총괄과 UI 담당에 전달했다.

UI 담당의 `node reports/team_20261111/ui/start-position-ui.test.cjs`도 독립 실행하여 DOM 경계/6축/미확인/1200ms/연결종료 검사 PASS 확인. 이 테스트 PASS가 위 별도 불일치를 없애지는 않는다. 실제 보정자료로 생성된 기준 JSON 검증과 변경 완료 후 새 지문 재검증은 남아 있다.

## 완료내용

기존 home 화면과 실행기 시작 위치 검사 차이를 장치 없는 모의 검증으로 재현했다. 최종 수정본 검증 PASS가 아니라 **기존 원인 재현 완료 / 통합 검증 대기** 상태다.

실행기는 정수 raw tick을 검사한다. elbow 범위 880–3082에 시작 측정용 상단 margin 1을 적용하므로 3083까지 허용한다. 목표 쓰기 상한 3082를 늘리는 의미는 아니다. 기존 화면은 home ±2만 검사하고 tick은 표시만 한다. home 정렬 OK는 전체 실기 준비완료가 아니다.

## 변경파일

- `reports/team_20261111/evaluation/check_baseline.py`: AST로 기존 validate_position 함수만 추출하고 HTML 스크립트를 Node VM의 가짜 DOM/WebSocket/시계에서 실행. 로봇 모듈 import 및 실제 연결 없음.
- `reports/team_20261111/evaluation/baseline_results.json`: 입력별 실제 결과와 SHA256 지문.
- 이 보고서. 기존 평가 스크립트/결과 수정 전 `.bak_타임스탬프` 보존.

## 검증결과

| 팔꿈치 raw tick | 제공 각도 | home 96.552176 대비 | 기존 화면 | 실행기 위치 검사 | 수정 화면 기대 |
|---|---:|---:|---|---|---|
| 3082 | 96.5714° | +0.0192° | home OK | 허용 | home OK / 시작 위치 허용 |
| 3083 | 96.6593° | +0.1071° | home OK | 허용 | home OK / 시작 위치 허용, 목표 상한은 3082 |
| 3084 | 96.7473° | +0.1951° | home OK | HOLD | home OK / 시작 위치 차단 |

8개 실행기 함수 사례와 11개 기존 HTML 사례 재현. raw 879 차단, 880 허용, float 3083.0/None/NaN 차단을 확인했다. 표의 각도는 제공된 검증값이며 실제 calibration 재추정이 아니다. 모든 허용은 위치 조건 하나만을 뜻한다.

기존 HTML에서 누락틱도 home OK가 된다. 1200ms 경과 시 대기 표시로 전환한다. NaN은 JSON 전송 시 null로 바뀌며 메시지를 거부하지만 직전 정상값은 1200ms 이내에 남아 home OK가 유지된다. 새 값으로 승인하는 것은 아니지만 현재 오류와 이전 정상값 구분이 불명확하다.

그리퍼는 wire 값에 180/pi를 곱해 0–100 **점**으로 복원한다. 몸 5축은 같은 변환 후 **도**다. 0점과 100점은 home(4.275478점) 밖, home±2점 경계는 안으로 재현됐다. 화면 단위와 0–100 범위, home 허용폭은 별도 조건이다.

## 통합 검증행렬 / 남은문제

| 추가 입력 / 조건 | 기대 결과 |
|---|---|
| 각 축 low−1, low, high, high+1 | 실행기와 동일 raw 판정, elbow high+1만 측정 margin 허용 |
| 목표 elbow 3083 | 시작 측정 margin으로 목표 범위를 확장하지 않음 |
| 누락 축 / null / NaN / Infinity / bool / 문자열 tick / 비정수 tick | 시작 위치 승인 금지, 오류 또는 미확인 명시 |
| tick 일부만 존재, degree는 모두 home | home 정렬과 시작 위치 미확인을 분리 |
| stale 1199ms, 1200ms, 연결 끊김 | 기존 신선도 경계 유지, stale/끊김 시 시작 승인 금지 |
| 정상 직후 invalid packet | 이전 값 유지 여부와 데이터 오류를 명시; 최신 정상값으로 오인 금지 |
| 몸 home±2°, ±2° 초과 | 표시 반올림 전 수치로 경계 판정 |
| gripper 0/100, home±2점, −1/101 | 단위 점, 물리 정규화 범위와 home 정렬 범위 구분 |
| 온도/토크/모드/장치/움직임 조건 미수신 | 전체 시작 준비완료로 표시하지 않음 |

실행기 검사 중 다른 담당의 코드가 갱신되어 최초 추출은 NameError로 중단했다. 그 뒤 담당이 보존한 `live_start.py.bak_20261111_161059`를 **기존 기준 전용**으로 명시하여 검증했다. 최신 파일을 덮어쓰지 않았으며 이 결과를 수정본 PASS로 사용하면 안 된다. 실제 HTML/실행기 수정본 경로와 완료 통보 후 새 지문으로 별도 검증해야 한다.

모델·학습데이터 전체분석, SSH, serial, 카메라, 모델 추론, 서비스 변경 및 로봇 동작은 수행하지 않았다. 학습 137회 / A red48 green45 blue44 / checkpoint20k는 전달받은 현황이며 이번 작업에서 재검증·변경하지 않았다.

## 총괄요청

수정 완료된 실행기·공통 규칙·HTML·관련 테스트 경로를 전달해 주세요. 변경 완료 통보 후 위 행렬로 동일판정을 검증하겠습니다. 현재 상태를 초기 재현 완료로만 취급하고 배포/실기 승인으로 사용하지 마세요.
