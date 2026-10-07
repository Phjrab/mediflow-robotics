# Astra RGB-D 현재 배치 진단 — 2026-10-05

## 안전 범위

- 카메라만 사용했다.
- 로봇 serial, torque, inference, 목표 관절값에 접근하지 않았다.
- 캡처를 위해 Astra V4L2 bridge를 잠시 멈춘 뒤 다시 시작했다.
- 종료 확인: `astra-v4l2-bridge.service=active`, `lelab-jetson-py310.service=active`, camera 8 HTTP 200.

## 캡처 결과

- Astra color/depth: 640×480, 30 FPS 설정
- color-depth registration: 활성화
- depth-color 동기화: 활성화
- 분석 프레임: 30
- 유효 처리 속도: 약 18.7 FPS
- 마커 ID 0~6: 검출
- depth 작업대 평면 RMS: 약 2.15 mm
- depth 작업대 rigid-fit RMS: 약 21.07 mm
- 기준점 거리 오차: 약 -15.1 mm ~ -49.8 mm
- RGB PnP 재투영 RMS 중앙값: 약 2.49 px

## 판정

`DEPTH_NOT_AUTHORIZED_FOR_ROBOT_TARGETS`

평면 자체는 비교적 안정적으로 보이지만, 작업대 기준점 간 거리 축척/변환 오차가
파지 작업에 허용하기에는 크다. 현재 depth 결과를 `T_B_W`, 픽업 XYZ 또는 로봇
waypoint로 사용하지 않는다. 독립적인 TCP 접촉점 보정이 필요하다.

## 보관 파일

- `openclaw_rgbd_current_rgb.png`: 동기화 RGB 원본
- `openclaw_rgbd_current_depth.png`: 동기화 uint16 depth
- `openclaw_rgbd_current_annotated.png`: ArUco/depth 진단 화면
- `openclaw_depth_candidates.png`: depth foreground 후보 화면
- `openclaw_depth_candidates.json`: 후보 수치
- `current_raw_after_user_placement_20261005.jpg`: V4L2 현재 배치 원본
- `scene_current_placement_20261005.json`: X를 쓰지 않은 약통 중심/마커 검사 결과
