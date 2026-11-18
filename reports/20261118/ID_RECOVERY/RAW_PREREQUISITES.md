# Raw 모드 선행 데이터 확인

확인일: 2026-11-18. **읽기 전용 분석 완료 / 137회 원본 전체 재계산은 NOT_RUN**.

원본 통계와 계산 코드를 확인했다. `action`은 리더 목표, `observation.state`는 팔로워 실제 위치이며 아래 변화량은 **action만** 계산한 값이다. 원본 보정·실행기·raw 코드·태그·GitHub는 변경하지 않았다.

## 프레임 주기와 집계

- 기존 137회 통계: **30 fps**, 63,551프레임, 63,414개 인접 프레임 쌍.
- 회차별로 frame_index 정렬 → 각 회차 안에서 `abs(diff(action))` → 합치므로 회차 경계 점프를 제외한다.
- `63,551 - 137 = 63,414`와 일치한다. red 48회, green 45회, blue 44회다.
- 기존 계산 소스는 각 source info의 fps=30 및 action 6축 이름/순서를 검사하고, timestamp 간격이 1/30초 ±0.001초인지 확인한다.
- 마지막 행 제외 기록: red 추가 source의 episode 39는 455행, green 추가분(원본 이름은 blue) source의 episode 0은 516행만 사용한다.
- 이번에는 원본 parquet 6개 중 **4개 SHA-256 일치**를 확인했다. 해당 로컬 meta/info.json은 모두 fps=30이다.
- 합친 three_color의 meta/info.json과 누락된 두 parquet를 찾지 못해 원본 전체를 새로 계산하지는 않았다. 확인된 info는 각 source의 전체 회차 수이고, 제외 후 선택 수나 합친 학습 회차 수와는 구분했다.

## 관절별 최대 action 변화량 × 1.5 (설정 미적용)

| 관절 | 단위 | 기존 통계 최대/프레임 | ×1.5 후보/프레임 |
|---|---|---:|---:|
| shoulder_pan | 도 | 7.296703339 | 10.945055008 |
| shoulder_lift | 도 | 14.417579651 | 21.626369476 |
| elbow_flex | 도 | 15.824175358 | 23.736263037 |
| wrist_flex | 도 | 12.307693481 | 18.461540222 |
| wrist_roll | 도 | 4.571428537 | 6.857142806 |
| gripper | 그리퍼 점 | 16.003063202 | 24.004594803 |

위 표는 30 Hz 학습 action 인접쌍의 변화량이며, 속도(도/초)나 60 Hz 보간 패킷 간 한계와 다르다. gripper는 도가 아닌 0~100 정규화 점수다. 이 수치는 물리 안전성 검증 결과가 아니고 설치하지 않았다.

## 원본 추적

| 자료 | SHA-256 |
|---|---|
| 기존 통계 JSON | `252d17501046f993510d044f56fe63c786d3a48ccbe4f7b64720d0abe6fff407` |
| 기존 계산 Python | `de1932924597bb7abce181531ed1e0d21fad7ff1f92da64834152d1ffcbf9f96` |
| 137회 episode_mapping.csv | `d6b2c82317ff8a1652f5dd6d3eb14e43899421257d19a3bf78caa0450927821a` |

매핑 해시는 기존 통계의 기록과 일치한다. `a_to_blue_20261102_212958`은 이름만 blue이고 매핑의 `green_additional40_named_blue` / `color=green`에 따라 35회를 green으로 집계했다. 실제 경로와 parquet별 해시는 `RAW_PREREQUISITES.json`에 기록했다.

| Source | 선택 회차 | 로컬 원본 상태 |
|---|---:|---|
| a_to_red_20261102_201029 | 9 | HASH_MATCH |
| a_to_red_20261102_210433 | 39 | NOT_FOUND_IN_WORKSPACE |
| a_to_green_20261102_201804 | 10 | HASH_MATCH |
| a_to_blue_20261102_212958 | 35 | NOT_FOUND_IN_WORKSPACE |
| a_to_blue_20261103_193835 | 35 | HASH_MATCH |
| a_to_blue_20261103_193353 | 9 | HASH_MATCH |

## 남은 확인

- 누락 두 source와 합친 학습 metadata를 확보해야 137회 전체를 재계산하고 최대값의 정확한 회차/프레임까지 붙일 수 있다.
- 현재 Python에는 numpy/pyarrow가 없고 이번 작업에서는 설치하지 않았다.
- 선행 ID/timing 검증, pre-raw 태그와 지정 GitHub 업로드는 이 보고서에서 완료로 판정하지 않는다. raw 구현은 변경하지 않았다.
- 목표·토크·설정 쓰기 0회, SSH 접속 0회, live 0회.
