# LeLab A/B 데이터셋 촬영 계획

## 확정 이름

LeLab의 이름 입력칸에는 계정명이나 `/`를 넣지 않고 아래의 **입력값만** 넣는다.
LeLab이 `Supermassive111/` 계정명과 촬영 시각을 자동으로 붙인다.

| 대상 | LeLab 이름 입력값 | 예상 최종 Repository ID | Task |
|---|---|---|---|
| B | `medicine_b_to_basket_b_3cam_v1` | `Supermassive111/medicine_b_to_basket_b_3cam_v1_YYYYMMDD_HHMMSS` | `Pick up medicine bottle B and place it into basket B.` |
| A | `medicine_a_to_basket_a_3cam_v1` | `Supermassive111/medicine_a_to_basket_a_3cam_v1_YYYYMMDD_HHMMSS` | `Pick up medicine bottle A and place it into basket A.` |

2026-09-28에 완료한 B 데이터는 이름 입력칸에 계정명까지 넣어서 실제 저장명이
`Supermassive111/Supermassive111_medicine_b_to_basket_b_3cam_v1_20260928_203749`
가 되었다. 이름만 중복됐을 뿐 데이터 구조와 학습에는 문제가 없으므로 임의로
이름을 바꾸지 않는다.

현재 C 기준 데이터셋은
`Supermassive111/medicine_c_to_basket_c_3cam_test_20260928_154245`이다.

세 데이터셋을 나중에 합친 결과의 권장 이름은
`Supermassive111/medicine_abc_matching_baskets_3cam_v1`이다.

## 공통 녹화 설정

- Episodes: `60`
- Episode time: `30초`
- Reset time: `15초`
- FPS: `30`
- 카메라:
  - `ceiling_vertical`
  - `ceiling_oblique`
  - `end_effector`
- A/B/C 약통을 모두 올려놓는다.
- 바구니는 왼쪽부터 A/B/C 순서로 고정한다.
- 한 에피소드에서는 지정된 약통 하나만 해당 바구니에 넣고 종료한다.
- 잘못된 약통을 건드리거나, 파지에 실패하거나, 바구니 밖에 떨어뜨린 에피소드는
  정상 데이터로 남기지 않고 다시 촬영한다.

## B 데이터 60회 배치표

아래 여섯 배치를 각각 10회씩 촬영한다. 표기 순서는 왼쪽-가운데-오른쪽이다.

| 회차 | 약통 배치 | 목표 |
|---:|---|---|
| 1-10 | B-A-C | B → B 바구니 |
| 11-20 | B-C-A | B → B 바구니 |
| 21-30 | A-B-C | B → B 바구니 |
| 31-40 | C-B-A | B → B 바구니 |
| 41-50 | A-C-B | B → B 바구니 |
| 51-60 | C-A-B | B → B 바구니 |

## A 데이터 60회 배치표

아래 여섯 배치를 각각 10회씩 촬영한다. 표기 순서는 왼쪽-가운데-오른쪽이다.

| 회차 | 약통 배치 | 목표 |
|---:|---|---|
| 1-10 | A-B-C | A → A 바구니 |
| 11-20 | A-C-B | A → A 바구니 |
| 21-30 | B-A-C | A → A 바구니 |
| 31-40 | C-A-B | A → A 바구니 |
| 41-50 | B-C-A | A → A 바구니 |
| 51-60 | C-B-A | A → A 바구니 |

## 에피소드 성공 기준

1. 시작 프레임에서 약통 3개와 바구니 3개가 천장 카메라에 보인다.
2. 로봇이 지정된 약통만 접근한다.
3. 약통을 안정적으로 파지해 바구니까지 이동한다.
4. 지정된 같은 문자 바구니 안에서 약통을 놓는다.
5. 그리퍼가 약통을 놓은 뒤 에피소드를 끝낸다.

## 통합 학습 원칙

- A/B/C 원본 데이터셋은 각각 별도로 보존한다.
- 합칠 때 세 Task 문자열을 그대로 유지해 각 에피소드의 목표가 구분되게 한다.
- A/B/C를 각 60회씩 확보하면 총 180에피소드의 균형 데이터가 된다.
- C 단독 학습 결과를 먼저 시험한 뒤 B와 A를 촬영한다. C 결과가 좋지 않으면
  실패 유형을 확인하고 동일한 유형의 보충 데이터를 별도 데이터셋으로 추가한다.
