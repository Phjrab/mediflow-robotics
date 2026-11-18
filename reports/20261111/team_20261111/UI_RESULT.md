# UI 담당 결과

## 완료내용

기존 `tmp/servo_b_step_20261109/home-joints.html`을 읽어 별도 UI를 작성했다. 홈 정렬과 VLA 시작 위치를 분리하고, 전체 상태는 두 조건을 함께 반영한다. 홈 목표/단위/허용오차와 시작 틱 범위는 실행기 exporter 계약 JSON을 사용한다. 3084는 홈 OK라도 빨간 HOLD이며 명령상한 3082 대비 2틱, 시작허용 3083 대비 1틱 초과 및 `3082틱≈96.57°` 안내를 표시한다. 방향은 추측하지 않는다.

6축 현재 틱과 명령/시작 범위를 표시한다. 동일 호스트 WS와 1200ms stale 제한을 유지한다. JSON 기준 로드 실패/누락/잘못된 계약, 틱 누락/잘못된 숫자 형식, malformed JSON/잘못된 joint_update, 연결 종료는 OK가 되지 않는다. invalid 관절 패킷은 이전 값을 즉시 폐기한다. JSON `3083.0`/지수 표기는 Python 정수 타입 계약과 맞추기 위해 JSON.parse reviver의 context.source 정수 표기 검사에서 거부한다. context.source 미지원 브라우저에서는 미확인으로 닫힌다. 별도 JSON 파서는 사용하지 않는다. 충족 문구는 `위치 조건 충족 · 온도/토크/장치는 실행 시 재확인`이다.

## 변경파일

- `reports/team_20261111/ui/home-joints.html`
- `reports/team_20261111/ui/start-position-ui.js`
- `reports/team_20261111/ui/start-position-ui.test.cjs`

각 수정 전 `.bak_yyyyMMdd_HHmmss` 백업을 같은 폴더에 보존했다. 서비스 원본과 다른 담당 파일은 수정하지 않았다.

## 검증결과

`node reports/team_20261111/ui/start-position-ui.test.cjs`: PASS. 모의 DOM으로 3082/3083/3084, 6축 범위 밖, 홈 불일치, 틱 누락/null/문자열/소수, 기준 오류, 1200ms stale, 연결 종료, same-origin WSS, JSON decimal/exponent/bool/null/string, 중복키의 Python 동일 last-value 의미, invalid 패킷 즉시 이전 OK 폐기를 확인했다. 테스트의 다른 축 범위와 sources 해시는 명시적인 합성 mock이며 장치 접근이 없다.

`node --check reports/team_20261111/ui/start-position-ui.js`: PASS.

## 남은문제

실제 calibration/home/base로 exporter를 실행한 JSON과의 최종 호환 검증은 총괄/평가 담당이 수행한다. 로컬에 실제 6축 calibration JSON은 없으므로 UI가 임의의 production JSON을 생성하지 않았다. 브라우저/SSH/serial/카메라/모델/서비스/모터 접근을 수행하지 않았다.

## 총괄요청

최종 독립 검증 후 HTML/JS/exporter의 `start-criteria.json`을 실제 dist의 같은 폴더에 함께 배포한다. sources는 `filename,role,sha256` 계약이다. `home.joint_names`는 `[shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper]` 순서다. 기존 기준 및 온도/토크/장치 실행 검사에는 변경이 없다. 배포와 읽기 전용 실제 표시 확인은 총괄이 수행한다.
