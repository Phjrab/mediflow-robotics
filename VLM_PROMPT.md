# MediFlow VLM 약통 인식·상태 판단·로봇팔 분류 개발 프롬프트

> 이 문서는 Codex/개발 에이전트가 MediFlow 프로젝트의 **VLM 약통 분류 파트**를 이어서 구현하기 위한 기준 문서이다.
> 현재 최우선 목표는 모델 학습이 아니라 **실제 카메라 환경에서 사용할 약통 데이터셋을 일관된 기준으로 수집·정리하는 것**이다.

---

## 0. 프로젝트 목표

MediFlow 시스템에서 카메라로 작업대의 약통을 관찰하고 다음을 판단한다.

1. 어떤 약통인가? (`A`, `B`, `C`, `unknown`)
2. 약통이 어떤 자세인가? (`upright`, `fallen`, `tilted`, `unknown`)
3. 현재 상태에서 로봇팔이 잡아도 되는가?
4. 바로 잡을 것인가, 자세를 먼저 보정할 것인가, 판단을 보류할 것인가?
5. 최종적으로 어느 바구니에 넣어야 하는가?
6. 판단 결과를 구조화된 JSON으로 로봇 제어 계층에 전달한다.

최종 예시:

```text
Camera
  ↓
Object Detection / ROI
  ↓
ArUco / Depth / Position Estimation
  ↓
VLM
  ├─ medicine_id
  ├─ orientation
  ├─ graspability
  ├─ required_action
  └─ target_bin
  ↓
Robot Action Planner
  ↓
SO-ARM101
  ↓
Pick / Reorient / Place
```

**중요:** VLM은 로봇의 관절각이나 실제 XYZ 좌표를 임의 생성하지 않는다.

---

# 1. 시스템 역할 분리

## 1.1 Camera

카메라는 작업대의 RGB 영상을 획득한다.

가능한 경우 Depth 카메라를 사용하여 거리 정보를 별도로 획득한다.

카메라 단계의 책임:

- RGB frame
- Depth frame (지원 시)
- timestamp
- camera calibration 정보

---

## 1.2 YOLO / Object Detector

YOLO의 역할은 **약통이 어디에 있는지 찾는 것**이다.

출력 예:

```json
{
  "bbox": [x1, y1, x2, y2],
  "class": "medicine_container",
  "confidence": 0.96
}
```

VLM에게 전체 화면을 무조건 넘기기보다 검출된 약통 ROI와 필요 시 전체 scene을 함께 제공할 수 있도록 설계한다.

---

## 1.3 ArUco / 좌표 추정

ArUco는 가능하면 다음 목적으로 사용한다.

- 기준 좌표계 설정
- 카메라 ↔ 작업대 좌표 변환
- 로봇 좌표계와 연결
- 위치/방향 보조
- calibration 검증

ArUco가 약통에 부착되는 설계라면 marker ID도 약통 식별의 보조 정보로 사용할 수 있다.

단, **약통 종류를 ArUco만 보고 결정하는 시스템으로 고정하지 않는다.**
VLM/vision 모델이 약통 외형을 실제로 구분할 수 있어야 한다.

---

# 2. VLM의 정확한 담당 범위

VLM은 다음 질문에 답한다.

### Q1. 무슨 약통인가?

```text
A
B
C
unknown
```

### Q2. 약통 상태는?

```text
upright
fallen
tilted
unknown
```

### Q3. 현재 상태에서 잡을 수 있는가?

```text
true
false
unknown
```

### Q4. 어떤 행동이 필요한가?

```text
grasp
reorient_then_grasp
retry_view
skip
human_check
```

### Q5. 어느 바구니로 가야 하는가?

기본 매핑:

```text
A → BIN_A
B → BIN_B
C → BIN_C
```

약통 종류가 확실하지 않으면 바구니를 임의 선택하지 않는다.

```text
unknown → NONE
```

---

# 3. 가장 중요한 설계 원칙

VLM에게 다음을 시키지 않는다.

- 로봇 관절각 직접 생성
- 확인되지 않은 XYZ 좌표 생성
- confidence가 낮은 약통을 강제로 A/B/C 중 하나로 선택
- 보이지 않는 라벨을 추측
- 가려진 물체를 확신해서 판단
- 충돌 가능성을 무시하고 grasp 명령 생성

VLM의 역할은 **semantic decision**이다.

실제 로봇 제어는 별도의 deterministic control layer가 담당한다.

---

# 4. 현재 최우선 작업 — 약통 데이터셋 구축

현재는 모델을 복잡하게 만들기 전에 데이터셋을 제대로 구축한다.

## 핵심 원칙

좋은 데이터셋은 단순히 사진 수가 많은 데이터셋이 아니다.

다음 조건이 중요하다.

```text
다양성
+ 실제 사용 환경과 유사성
+ 클래스 균형
+ 정확한 라벨
+ 중복 최소화
+ 어려운 상황 포함
```

---

# 5. 약통 클래스 정의

현재 약통 종류:

```text
medicine_A
medicine_B
medicine_C
```

추후 약통 종류가 추가되어도 구조를 변경하지 않고 class mapping 파일만 수정할 수 있도록 구현한다.

예:

```yaml
medicine_map:
  A:
    target_bin: BIN_A

  B:
    target_bin: BIN_B

  C:
    target_bin: BIN_C
```

---

# 6. Pose / Orientation 기준

데이터를 찍기 전에 반드시 이 기준을 통일한다.

## 6.1 upright

약통이 정상적으로 바닥면을 아래로 하여 서 있는 상태.

예:

```text
   │ │
   │ │
   │ │
  ─────
```

판단 기준:

- 정상 바닥면으로 지지됨
- 실제 로봇 작업에서 '서 있는 약통'으로 취급 가능

---

## 6.2 fallen

약통이 옆으로 완전히 쓰러진 상태.

```text
────────
```

예:

- 약통 옆면이 작업대와 접촉
- 정상 세로축이 작업대와 거의 평행

---

## 6.3 tilted

완전히 서 있지도 않고 완전히 누워 있지도 않은 상태.

예:

```text
    /
   /
  /
──────
```

다른 물체에 기대어 있거나 비정상적으로 기울어진 경우도 포함한다.

---

## 6.4 unknown

다음과 같은 경우 사용한다.

- 심한 가림
- 화면 밖으로 대부분 잘림
- 자세 판단 불가능
- 다른 약통 뒤에 거의 숨음
- 영상 품질 문제

애매한 이미지를 억지로 upright/fallen으로 라벨링하지 않는다.

---

# 7. 데이터 수집 기본 원칙

A/B/C 각각 **비슷한 양의 데이터**를 확보한다.

초기 목표 예시:

| 조건 | 약통별 권장 수량 |
|---|---:|
| upright | 100+ |
| fallen | 100+ |
| tilted | 50+ |
| 다양한 회전/라벨 방향 | 100+ |
| 거리 변화 | 50+ |
| 조명 변화 | 50+ |
| 부분 가림 | 50+ |
| 여러 약통 동시 등장 | 100+ |

위 숫자는 절대적인 최종값이 아니다.

초기 데이터 수집 시 클래스/상태 편향을 막기 위한 가이드로 사용한다.

---

# 8. 반드시 변화시켜야 하는 촬영 조건

## 8.1 약통 회전

같은 약통이라도 다음 방향을 포함한다.

```text
0°
45°
90°
135°
180°
225°
270°
315°
```

정확히 각도를 측정할 필요는 없지만 특정 정면만 반복해서 촬영하지 않는다.

반드시 포함:

- 라벨 정면
- 라벨 측면
- 라벨 후면
- 라벨 일부만 보임

---

## 8.2 위치

작업대 중앙만 촬영하지 않는다.

```text
좌상
상단
우상

좌측
중앙
우측

좌하
하단
우하
```

카메라 실제 FOV 전체를 사용한다.

---

## 8.3 거리

예:

```text
near
medium
far
```

실제 로봇이 작업할 수 있는 범위 내에서 변경한다.

---

## 8.4 조명

포함해야 할 상황:

- 정상 조명
- 조금 어두운 환경
- 조금 밝은 환경
- 그림자 발생
- 약통 한쪽에 그림자
- 반사 발생
- 카메라 auto exposure 변화

단, 실제 시스템에서 절대로 발생하지 않을 극단적인 조건을 억지로 많이 만들 필요는 없다.

---

# 9. 약통 상태별 촬영

각 약통마다 다음을 촬영한다.

## upright

- 정면
- 후면
- 좌측
- 우측
- 대각선
- 카메라에서 가까움
- 멀리 있음
- 화면 중앙
- 화면 가장자리

## fallen

쓰러진 방향을 다양화한다.

```text
←
→
↖
↗
↙
↘
```

라벨이 위를 보는 경우만 촬영하지 않는다.

반드시:

- 라벨 위
- 라벨 아래/안 보임
- 라벨 측면
- 뚜껑 방향 변화

를 포함한다.

## tilted

- 약간 기울어짐
- 크게 기울어짐
- 다른 약통에 기대어 있음
- 바구니/구조물에 기대어 있음

---

# 10. 여러 약통이 동시에 존재하는 Scene

실제 시스템에서는 한 프레임에 여러 약통이 존재할 수 있다.

따라서 다음을 반드시 포함한다.

```text
A + B
A + C
B + C
A + B + C

A + A
B + B
C + C

A + A + B
A + B + C
```

그리고 자세도 섞는다.

예:

```text
A upright
B fallen
C tilted
```

---

# 11. Occlusion 데이터

부분 가림 상황을 포함한다.

### Level 0

가림 없음.

### Level 1

약 10~25% 가림.

### Level 2

약 25~50% 가림.

### Level 3

50% 이상 가려져 사람도 판단이 어려움.

Level 3은 `unknown` 또는 별도 hard-case 데이터로 관리하는 것을 우선 고려한다.

---

# 12. 반드시 포함해야 할 Hard Case

다음 장면은 매우 중요하다.

- 약통끼리 붙어 있음
- 약통끼리 일부 겹침
- 하나는 서 있고 하나는 쓰러짐
- 화면 가장자리에 약통 존재
- 약통 일부가 화면 밖
- 라벨이 카메라 반대 방향
- 그림자가 약통을 가림
- 반사광 발생
- 비슷한 색의 배경
- 로봇팔이 약통 일부를 가림
- gripper가 약통 근처에 있음
- 다른 물체가 함께 존재
- 약통이 바구니 근처에 있음
- 여러 약통이 매우 가까이 있음

모델은 '깨끗한 전시 사진'이 아니라 실제 로봇 작업 환경에서 동작해야 한다.

---

# 13. 수집하면 안 되는 데이터

다음 이미지는 기본 학습 데이터에서 제외한다.

### 13.1 완전 초점 실패

약통 종류를 사람도 판단하기 어려운 수준.

### 13.2 심각한 Motion Blur

형태 자체가 무너지는 경우.

### 13.3 약통 대부분이 화면 밖

예:

약통의 70~80%가 화면 밖에 있고 일부만 보이는 경우.

단, robustness 평가용 hard test set으로 별도 저장할 수 있다.

### 13.4 의미 없는 중복

영상에서 다음처럼 연속 프레임을 그대로 추출하지 않는다.

```text
frame_001
frame_002
frame_003
frame_004
...
```

카메라와 약통이 거의 움직이지 않았다면 사실상 같은 데이터다.

500개의 거의 동일한 프레임보다 서로 다른 조건의 100개 이미지가 더 중요하다.

---

# 14. 영상에서 이미지 추출 시 중복 방지

영상으로 데이터를 수집한다면 일정 FPS마다 무조건 저장하지 않는다.

예:

30 FPS 영상에서 매 프레임 저장 금지.

초기 기준:

```text
0.5 ~ 2초 간격 후보 추출
```

후보 프레임 사이에서 다음이 실제로 달라졌는지 확인한다.

- 약통 위치
- 약통 회전
- pose
- 카메라 위치
- 조명
- 다른 물체 배치

가능하면 perceptual hash 또는 image similarity 기반 중복 제거 기능을 추가한다.

---

# 15. 이미지 채택 기준

이미지를 저장하기 전에 다음을 확인한다.

```text
[ ] 약통이 실제 작업 영역에 존재한다.
[ ] 사람이 약통 종류를 판별할 수 있다.
[ ] orientation을 판별할 수 있다.
[ ] 이미지가 지나치게 흐리지 않다.
[ ] 이전 이미지와 사실상 동일하지 않다.
[ ] 현재 조건이 데이터 다양성에 도움이 된다.
```

모두 만족하면 일반 dataset에 저장한다.

애매하면:

```text
review/
```

폴더로 이동한다.

---

# 16. 폴더 구조

권장 구조:

```text
dataset/
│
├─ raw/
│  ├─ session_001/
│  ├─ session_002/
│  └─ session_003/
│
├─ images/
│  ├─ train/
│  ├─ val/
│  └─ test/
│
├─ labels/
│  ├─ train/
│  ├─ val/
│  └─ test/
│
├─ metadata/
│
├─ review/
│
├─ rejected/
│
└─ config/
   └─ medicine_map.yaml
```

원본 `raw/` 파일은 가능하면 삭제하지 않는다.

---

# 17. 파일명 규칙

파일명만 봐도 대략적인 정보를 확인할 수 있게 한다.

예:

```text
A_upright_front_s001_0001.jpg
A_fallen_side_s001_0002.jpg
B_tilted_back_s002_0013.jpg
C_fallen_front_s003_0032.jpg
```

권장 포맷:

```text
{medicine}_{orientation}_{view}_{session}_{index}.jpg
```

단, 학습 label의 진실값을 파일명에만 의존하지 않는다.

metadata에도 기록한다.

---

# 18. Metadata

각 이미지에 다음 정보를 기록할 수 있도록 한다.

```json
{
  "image_id": "A_fallen_side_s001_0002",
  "medicine_id": "A",
  "orientation": "fallen",
  "view": "side",
  "occlusion": "none",
  "distance": "medium",
  "lighting": "normal",
  "scene_id": "scene_001",
  "session_id": "session_001",
  "graspable": true,
  "notes": ""
}
```

향후 필요한 항목을 추가할 수 있도록 parser를 유연하게 작성한다.

---

# 19. Train / Validation / Test 분리

가장 중요한 규칙 중 하나다.

**연속 촬영한 거의 동일한 이미지가 train과 test에 동시에 들어가면 안 된다.**

잘못된 예:

```text
frame 100 → train
frame 101 → train
frame 102 → test
```

이렇게 하면 실제보다 성능이 높게 측정될 수 있다.

따라서 `scene_id` 또는 `session_id` 기준으로 분리한다.

예:

```text
Session 1~7 → Train
Session 8   → Validation
Session 9~10 → Test
```

또는 대략:

```text
Train 70%
Validation 15%
Test 15%
```

단순 랜덤 이미지 분할보다 **scene/session 단위 분할을 우선한다.**

---

# 20. Test Set은 일부러 어렵게 구성

Test set에는 다음을 반드시 포함한다.

- 새로운 약통 위치
- 새로운 회전
- 다른 조명
- 부분 가림
- 여러 약통
- 로봇팔 일부 등장
- 쓰러진 약통
- tilted
- 화면 가장자리
- 라벨이 안 보이는 방향

Train과 거의 같은 장면만 Test로 사용하는 것을 금지한다.

---

# 21. Graspability

VLM은 약통 상태뿐 아니라 `graspable`을 판단할 수 있다.

단, 최종 충돌 검사는 로봇 제어 계층이 담당한다.

초기 의미:

## graspable = true

시각적으로 보았을 때:

- 약통이 충분히 노출됨
- 다른 약통과 심하게 겹치지 않음
- gripper 접근이 명백히 불가능해 보이지 않음

## graspable = false

예:

- 다른 약통 밑에 깔림
- 대부분 가려짐
- 벽/바구니 등에 지나치게 붙음
- 다른 물체 사이에 끼어 있음

## unknown

이미지만으로 확신할 수 없는 경우.

VLM의 graspable은 **시각적 사전 판단**이며 실제 IK/충돌검사의 대체물이 아니다.

---

# 22. Required Action 정의

## grasp

현재 자세 그대로 집는다.

```text
upright → grasp
```

또는 쓰러져 있어도 로봇의 grasp policy가 직접 집을 수 있으면:

```text
fallen → grasp
```

---

## reorient_then_grasp

현재 상태에서는 안정적으로 집기 어렵고 먼저 자세 보정이 필요한 경우.

예:

```text
fallen
+
graspable=false
+
재정렬 가능
```

로봇 동작 예:

```text
push / roll / stand
↓
camera re-check
↓
grasp
```

**중요:** 실제로 '세우기'가 필요한지는 SO-ARM101의 gripper와 grasp 전략을 실험한 뒤 결정한다.

쓰러진 약통을 바로 집을 수 있다면 불필요하게 세우지 않는다.

---

## retry_view

판단 정보가 부족하다.

예:

- 심한 가림
- detection 불안정
- 약통 종류 불확실
- pose 불확실

가능하면 카메라/로봇 위치 변경 후 재촬영한다.

---

## skip

현재 물체 처리를 건너뛴다.

---

## human_check

자동 판단을 신뢰하기 어려운 경우 사람이 확인한다.

---

# 23. VLM 입력

가능하면 다음 정보를 VLM에 제공한다.

```text
1. 전체 scene 이미지
2. 약통 ROI crop
3. detector confidence
4. bbox
5. ArUco 정보 (있는 경우)
6. depth/거리 정보 (있는 경우)
```

예:

```json
{
  "image": "...",
  "crop": "...",
  "detector": {
    "bbox": [312, 205, 510, 620],
    "confidence": 0.97
  },
  "aruco": {
    "detected": true,
    "id": 3
  },
  "depth_mm": 642
}
```

---

# 24. VLM 출력 JSON

VLM은 자유로운 설명문 대신 우선 JSON을 반환한다.

필수 schema:

```json
{
  "medicine_id": "A",
  "orientation": "fallen",
  "graspable": true,
  "required_action": "grasp",
  "target_bin": "BIN_A",
  "confidence": 0.94,
  "reason": "Medicine A is visible and lying on its side with sufficient exposed area for grasping."
}
```

---

# 25. 허용 값

## medicine_id

```text
A
B
C
unknown
```

## orientation

```text
upright
fallen
tilted
unknown
```

## graspable

```text
true
false
null
```

## required_action

```text
grasp
reorient_then_grasp
retry_view
skip
human_check
```

## target_bin

```text
BIN_A
BIN_B
BIN_C
NONE
```

---

# 26. Abstention 규칙

VLM이 모르면 반드시 모른다고 할 수 있어야 한다.

예:

```json
{
  "medicine_id": "unknown",
  "orientation": "unknown",
  "graspable": null,
  "required_action": "retry_view",
  "target_bin": "NONE",
  "confidence": 0.31,
  "reason": "The container is heavily occluded."
}
```

다음 규칙을 강제한다.

```text
medicine_id == unknown
→ target_bin = NONE

confidence < threshold
→ robot pick 금지
→ retry_view 또는 human_check
```

threshold는 실험을 통해 결정하고 config 파일로 분리한다.

---

# 27. 바구니 매핑

바구니 매핑은 코드 곳곳에 hard coding하지 않는다.

```yaml
bins:
  A: BIN_A
  B: BIN_B
  C: BIN_C
```

향후 변경이 쉬워야 한다.

---

# 28. VLM → Robot Planner 인터페이스

VLM 결과:

```json
{
  "medicine_id": "B",
  "orientation": "upright",
  "graspable": true,
  "required_action": "grasp",
  "target_bin": "BIN_B",
  "confidence": 0.96
}
```

Robot Planner가 이를 받아:

```text
1. 대상 bbox 확인
2. depth 확인
3. camera coordinate 계산
4. robot coordinate 변환
5. grasp pose 계산
6. collision check
7. IK
8. robot move
9. gripper close
10. BIN_B 이동
11. release
```

를 수행한다.

VLM은 3D 좌표를 추측하지 않는다.

---

# 29. 로봇팔 동작 후 반드시 재확인

로봇팔이 동작했다고 성공으로 간주하지 않는다.

예:

```text
Pick command
↓
Robot motion
↓
Camera capture
↓
약통이 실제로 집혔는가?
↓
YES → basket 이동
NO  → retry
```

Place도 동일하다.

```text
Release
↓
Camera check
↓
올바른 바구니에 들어갔는가?
```

가능하면 closed-loop 방식으로 구현한다.

---

# 30. 데이터 수집 프로그램 요구사항

Codex는 먼저 데이터 수집 도구부터 구현한다.

필수 기능:

```text
[1] Camera preview
[2] Capture
[3] medicine 선택 A/B/C
[4] orientation 선택
[5] view 선택
[6] occlusion 선택
[7] 자동 filename 생성
[8] metadata 저장
[9] reject 기능
[10] capture counter
```

UI 예:

```text
Medicine:
[A] [B] [C]

Pose:
[Upright] [Fallen] [Tilted] [Unknown]

View:
[Front] [Side] [Back] [Other]

Occlusion:
[None] [Low] [Medium] [High]

[C] Capture
[R] Reject Last
[Q] Quit
```

가능하면 OpenCV 기반으로 단순하게 시작한다.

---

# 31. 자동 품질 검사

저장 전에 다음 검사를 추가할 수 있다.

### Blur 검사

Laplacian variance 등으로 지나치게 흐린 사진 감지.

단, 자동 삭제하지 않는다.

```text
QUALITY WARNING: BLUR
```

표시 후 사용자가 선택하도록 한다.

### 밝기 검사

평균 brightness가 지나치게 낮거나 높은 경우 경고.

### Duplicate 검사

perceptual hash 등을 이용해 거의 같은 이미지인지 검사.

---

# 32. 데이터 수집 Session

한 번의 촬영을 하나의 session으로 관리한다.

예:

```text
session_001
session_002
session_003
```

가능하면 session마다 조금씩 조건을 바꾼다.

예:

```text
session_001
normal lighting

session_002
different object positions

session_003
darker lighting

session_004
multiple containers

session_005
robot arm visible
```

이렇게 해야 train/test 분리가 쉬워진다.

---

# 33. 권장 초기 촬영 순서

처음부터 무작위로 찍지 않는다.

### STEP 1

A만 사용.

```text
upright
fallen
tilted
```

수집 파이프라인 검증.

### STEP 2

B 추가.

### STEP 3

C 추가.

### STEP 4

A/B/C 동일한 조건으로 균형 맞춤.

### STEP 5

여러 약통 scene.

### STEP 6

가림 / 그림자 / 위치 변화.

### STEP 7

SO-ARM101을 화면에 넣은 실제 작업 scene.

### STEP 8

로봇이 약통을 잡기 직전/직후 scene.

---

# 34. 현재 단계에서 하지 말 것

현재 데이터가 충분히 모이기 전에 다음 작업부터 과도하게 진행하지 않는다.

- VLM fine-tuning 최적화
- 복잡한 agent architecture
- 로봇 autonomous loop 완성
- 여러 모델을 무작정 비교
- prompt engineering만 반복

우선:

```text
Camera
↓
Dataset
↓
Labels
↓
Baseline
```

을 안정화한다.

---

# 35. Baseline 구현 순서

## Phase 1 — Camera

완료 조건:

- 안정적으로 frame 획득
- 해상도 확인
- calibration 완료
- RGB/Depth 정합 확인(Depth 사용 시)

## Phase 2 — Dataset Collector

완료 조건:

- 버튼/키 입력으로 저장 가능
- metadata 자동 생성
- session 관리 가능
- A/B/C와 pose 저장 가능

## Phase 3 — Dataset QA

완료 조건:

- 클래스별 수량 출력
- pose별 수량 출력
- duplicate 후보 탐지
- blur 후보 탐지
- 잘못된 metadata 탐지

## Phase 4 — Detection

완료 조건:

- 약통 bbox 안정적으로 검출
- 여러 약통 동시 검출

## Phase 5 — VLM Baseline

완료 조건:

VLM이 다음 JSON을 안정적으로 생성:

```text
medicine_id
orientation
graspable
required_action
target_bin
confidence
```

## Phase 6 — Position Estimation

완료 조건:

```text
pixel
→ camera coordinate
→ robot coordinate
```

변환 검증.

## Phase 7 — Robot Pick

단일 약통부터 테스트.

## Phase 8 — Sorting

```text
A → BIN_A
B → BIN_B
C → BIN_C
```

## Phase 9 — Hard Case

쓰러짐/가림/다중 약통 처리.

---

# 36. 성능 평가

단순 전체 Accuracy 하나만 보지 않는다.

최소 다음을 기록한다.

```text
medicine classification accuracy
orientation accuracy
graspability accuracy
action accuracy
target_bin accuracy
unknown/abstention accuracy
false confident answer rate
```

특히 위험한 오류:

```text
A를 B라고 판단
→ BIN_B에 넣음
```

따라서 `wrong-bin rate`를 별도로 측정한다.

---

# 37. Safety Rule

아래 조건 중 하나라도 만족하면 로봇 실행을 중단한다.

```text
medicine unknown
low confidence
invalid JSON
target_bin NONE
coordinate unavailable
depth invalid
collision check failed
IK failed
multiple target ambiguity
```

기본 정책:

```text
WHEN UNCERTAIN:
DO NOT MOVE.
RE-CAPTURE OR REQUEST CHECK.
```

---

# 38. Codex 구현 규칙

이 프로젝트의 코드를 수정할 때 다음을 지킨다.

1. 기존 동작 코드를 무조건 갈아엎지 않는다.
2. 먼저 repository 구조를 확인한다.
3. 기존 camera/calibration 코드를 재사용한다.
4. 하드웨어 의존 값은 config로 분리한다.
5. medicine→bin mapping도 config로 분리한다.
6. VLM 출력에는 JSON schema validation을 적용한다.
7. 실패 시 로봇이 움직이지 않는 방향으로 설계한다.
8. 각 단계에 logging을 추가한다.
9. 이미지와 판단 결과를 추적 가능하게 기록한다.
10. 변경 후 실행 방법을 README에 갱신한다.

---

# 39. 권장 모듈 구조

기존 repository와 충돌하지 않는 경우 다음 구조를 참고한다.

```text
src/
├─ camera/
│  ├─ capture.py
│  ├─ depth.py
│  └─ calibration.py
│
├─ dataset/
│  ├─ collector.py
│  ├─ metadata.py
│  ├─ quality_check.py
│  └─ split_dataset.py
│
├─ detection/
│  └─ detector.py
│
├─ vlm/
│  ├─ client.py
│  ├─ prompt.py
│  ├─ schema.py
│  └─ decision.py
│
├─ localization/
│  ├─ aruco.py
│  └─ transform.py
│
├─ robot/
│  ├─ planner.py
│  ├─ controller.py
│  └─ safety.py
│
└─ main.py
```

단, 기존 repository 구조가 있다면 기존 구조를 우선한다.

---

# 40. 지금 Codex에게 시킬 첫 번째 작업

다음 순서대로 진행한다.

## TASK 1

현재 repository 전체 구조를 분석한다.

다음 파일을 찾는다.

```text
camera 관련 코드
calibration 관련 코드
ArUco 관련 코드
SO-ARM101 관련 코드
VLM 관련 코드
config
README
requirements
```

기존 구현을 먼저 설명하고 함부로 수정하지 않는다.

## TASK 2

현재 카메라로 RGB 이미지를 안정적으로 저장할 수 있는지 확인한다.

Depth 카메라라면 RGB와 Depth를 각각 확인한다.

## TASK 3

`dataset_collector`를 구현한다.

최소 입력:

```text
medicine_id
orientation
view
occlusion
```

Capture 시:

```text
image 저장
+
metadata JSON/CSV 저장
```

## TASK 4

촬영 통계를 출력한다.

예:

```text
A:
 upright  : 83
 fallen   : 74
 tilted   : 31

B:
 upright  : 79
 fallen   : 81
 tilted   : 28

C:
 upright  : 85
 fallen   : 76
 tilted   : 30
```

부족한 조건을 바로 확인할 수 있어야 한다.

## TASK 5

Dataset QA script를 구현한다.

출력:

```text
class imbalance
pose imbalance
blur candidates
duplicate candidates
missing metadata
invalid labels
```

---

# 41. Codex에게 주는 현재 실행 명령

이 문서를 읽은 뒤 바로 모델 학습부터 시작하지 말 것.

먼저:

1. repository를 조사한다.
2. 현재 구현된 camera/calibration 상태를 파악한다.
3. 기존 코드 중 재사용할 부분을 찾는다.
4. 데이터 수집 파이프라인 설계안을 제시한다.
5. 필요한 파일 변경 목록을 제시한다.
6. 그 후 dataset collector를 구현한다.

불확실한 하드웨어 API나 카메라 SDK 함수명을 추측해서 코드를 만들지 말고 실제 repository와 설치된 dependency를 확인한다.

---

# 42. 최종 목표 시나리오

```text
카메라 촬영
      ↓
약통 검출
      ↓
A/B/C 식별
      ↓
Pose 판단
 ┌────┼─────┐
upright fallen tilted
 └────┼─────┘
      ↓
잡기 가능 여부 판단
      ↓
 ┌───────────────┐
 │               │
YES              NO
 │               │
grasp      reorient/retry
 │               │
 └───────┬───────┘
         ↓
    위치 계산
         ↓
    충돌/IK 검사
         ↓
    SO-ARM101
         ↓
      약통 Pick
         ↓
    카메라 재확인
         ↓
 medicine_id 확인
         ↓
 A → BIN_A
 B → BIN_B
 C → BIN_C
         ↓
       Place
         ↓
    결과 재확인
```

---

# 43. 프로젝트 핵심 원칙 요약

```text
YOLO = 어디 있는가

ArUco / Depth / Calibration
= 실제 공간에서 어디 있는가

VLM
= 무엇이며 어떤 상태이고 무엇을 해야 하는가

Robot Planner
= 어떻게 안전하게 움직일 것인가

SO-ARM101
= 실제 행동 수행
```

**VLM에게 모든 것을 맡기지 않는다.**

특히:

```text
VLM → 의미 판단
Geometry → 좌표 계산
Planner → 동작 결정/검증
Robot → 실행
Camera → 결과 재확인
```

으로 역할을 분리한다.

---

# 44. 현재 프로젝트 우선순위

```text
[현재]
카메라 안정화
    ↓
약통 데이터셋 수집  ← 지금 여기
    ↓
데이터 QA
    ↓
YOLO/ROI
    ↓
VLM 약통 + Pose 판단
    ↓
ArUco/Depth 좌표 연결
    ↓
SO-ARM101 Pick
    ↓
A/B/C Basket Sorting
    ↓
쓰러진 약통 처리
    ↓
Closed-loop 재확인
    ↓
전체 MediFlow 통합
```

이 순서를 기본 개발 방향으로 유지한다.
