# 홈 화면과 VLA 시작 위치 기준 통합 결과

기록일: 2026-11-11. 이번 범위는 코드·모의 테스트·Jetson 배포·웹 읽기 확인이다.

## 원인과 변경

최근 실행은 팔꿈치 3084틱에서 시작이 거부됐고 0스텝, 목표 전송 0회였다. 기존 홈 화면은 홈 평균과 ±2도만 비교하여 OK를 표시했다. 실행기는 별도로 보정 범위와 팔꿈치 시작 상한 3083틱(명령 상한 3082 + 측정 여유 1)을 검사한다. 서로 다른 조건을 같은 준비완료 표시처럼 보여 준 것이 문제였다.

홈 평균·허용폭과 실제 원시 위치 기준을 내보내는 순수 모듈을 만들고, 웹과 실행기가 그 기준을 사용하도록 연결했다. 화면에는 홈 정렬 / 현재 틱 / VLA 시작 위치 조건을 각각 표시한다. 기준·원시 틱 누락, 잘못된 데이터, 연결 끊김, 1.2초 지난 데이터는 미확인이다. 위치 조건 통과는 온도·토크·카메라까지 통과했다는 뜻이 아니다.

| 팔꿈치 위치 | 기존 실행기 판정 = 새 웹 판정 |
|---|---|
| 3082틱 / 약 96.57도 | 시작 위치 조건 통과 |
| 3083틱 / 약 96.66도 | 시작 측정 여유 안 |
| 3084틱 / 약 96.75도 | 범위 밖. 홈 정렬만 통과할 수 있음 |

실제 제어 목표 제한, 보정·온도·토크·추론·텔레옵 동작은 바꾸지 않았다. 이 수정으로 범위 밖 자세를 허용하거나 로봇을 자동 이동시키지는 않는다.

## 파일

- 실행기: `tmp/vla_direct_live_v3_20261109/live_start.py`, `start_position_rules.py`, `export_start_criteria.py`, `tests_start_position_rules.py`.
- 웹: `reports/team_20261111/ui/home-joints.html`, `start-position-ui.js`, `start-position-ui.test.cjs`.
- 독립 검증: `reports/team_20261111/evaluation/`, `DATA_EVAL_RESULT.md`.
- 배포 도구: `reports/team_20261111/deploy_home_start.py`, `home_start_alignment_20261111_v2.zip`.
- 실제 Jetson 기준: `reports/team_20261111/jetson-start-criteria.json`.
- 담당 채팅·파일 소유권: `reports/team_20261111/TEAM.md`.

Jetson 적용 위치:

```
~/vla_direct_live_v3_20261109/live_start.py
~/vla_direct_live_v3_20261109/start_position_rules.py
~/vla_direct_live_v3_20261109/export_start_criteria.py
~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/frontend/dist/home-joints.html
~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/frontend/dist/start-position-ui.js
~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/frontend/dist/start-criteria.json
```

배포 및 백업: `~/home_start_alignment_20261111_v2/backup/`, `deployment_receipt.json`. 기존 live_start.py와 home-joints.html을 먼저 백업했다. 원본 보정, home JSON, direct_live.py, stream_live.py는 배포 전후 해시 일치를 확인했다.

## 실행과 결과

| 위치 | 명령·확인 | 결과 |
|---|---|---|
| 노트북 | `python -B tmp/vla_direct_live_v3_20261109/tests_start_position_rules.py` | 6개 통과, 종료코드 0 |
| 노트북 | `node reports/team_20261111/ui/start-position-ui.test.cjs` | UI 테스트 3그룹 통과, 종료코드 0 |
| 평가 담당 | `evaluation/wire_v3/cross_runtime_results.json` | 74사례 불일치 0 |
| 평가 담당 | `evaluation/reviver_compat.test.cjs` | JSON 정수·중복키·구형 기능 미지원 처리 통과 |
| 평가 담당 | `evaluation/production_results.json` | 실제 기준 27사례 불일치 0, 6축·홈·단위 확인 |
| Jetson | `~/venvs/smolvla-py310/bin/python -B ~/home_start_alignment_20261111_v2/tests_start_position_rules.py` | 6개 통과 |
| Jetson | 같은 환경에서 `deploy_home_start.py` / `--apply` | DEPLOY_CHECK_PASS → DEPLOYED |
| Jetson | `systemctl --user start lelab-jetson-py310.service` | active |
| Jetson/노트북 | 홈 HTML, JS, 기준 JSON GET | HTTP 200 |
| Jetson | 설치된 exporter 출력과 웹 기준 JSON 비교 | 완전히 일치 |
| Jetson | `/teleoperation-status`, `/inference-status`, `/recording-status` | 모두 비활성 |
| 브라우저 | 홈 페이지 새로고침 | 분리된 홈/VLA 기준 표시 확인; 관절값 대기 |

첫 v1 배포 사전검사는 기존 HTML 해시를 옮겨 적은 값의 오류로 중단했다. 설치 파일 변경은 없었다. 실제 파일과 로컬 원본의 같은 해시를 재확인해 v2로 수정한 뒤 적용했다. 상태 확인 첫 URL `/api/status`는 404였고, 실제 소스에 있는 세 상태 경로로 정정해 확인했다.

## 남은 확인

- 텔레옵을 실행하지 않았으므로 새 화면의 실제 모터 방송 수신은 NOT_RUN. 현재 화면이 대기인 것은 정상이다.
- 브라우저의 JSON 원문 정수 확인 기능 미지원 시 위치 조건은 미확인으로 처리한다. 구형 브라우저는 별도 실기 검증하지 않았다.
- 최신 실제 관절 위치·온도·토크는 이번 작업에서 읽지 않았다. 이전 실행의 3084틱을 현재값으로 단정하지 않는다.
- 로봇 동작, 카메라 열기, 모델 추론, live, push는 NOT_RUN.

## 되돌리기

텔레옵/VLA가 중지된 상태에서 배포 receipt의 `before_sha256`와 백업을 확인하고, `backup/live_start.py` 및 `backup/home-joints.html`을 각각 원래 위치에 복원하면 기존 동작·화면으로 돌아간다. 새 모듈/JS/JSON은 구 코드에서 사용하지 않으므로 삭제할 필요가 없다. 백업과 중단 기록은 보존한다. 되돌리기는 실행하지 않았다.
