# 카메라 정렬 도구 — 2026-11-15

관제 `http://100.64.203.77:8766/`에3대 카메라의 학습 첫 프레임 겹쳐 보기, 투명도, 카메라별 표시 전환·확대, 수직 약통·바구니 참조 윤곽을 추가했다. 실제 브라우저에서3대 영상·겹침 ON/OFF·사선 확대·수치를 확인했다. `alignment-dashboard.jpg`가 검증 화면이다.

겹침과 윤곽은 브라우저에만 그린다. 카메라 원본과 모델 입력은 수정하지 않는다. 윤곽은 학습 사진에서 표시한 참조 외곽이며 현재 약통 검출 결과가 아니다. 모터 접근/목표·토크·보정/PID/가속도 쓰기는0회다.

## 현재 측정과 판정

| 카메라 | 학습 대비 위치 차이 | 영상 내 회전 | 가로/세로 | 고정 마커 | 판정 |
|---|---:|---:|---|---|---|
| 손목 | 마커 없음 | 미측정 | 겹친 그리퍼·배경으로 수동 확인 | 0/4 | 겹침으로 확인 |
| 천장 수직 | 약1.0px | 약+0.15° | +0.5/-1.0px | 4/4 | 위치 차이≤20px |
| 천장 사선 | 약186.1px | 약-0.59° | +186/+4px | 2/4 | 조정 필요 |

위 값은21:42 측정8표본 중 마지막 값이다. 실시간 수치는 사이트에서 계속 바뀐다. 현재 사선 화면 속 기준판은 학습보다 오른쪽으로 약186px 치우쳐 있다. 화면 속 기준판이 왼쪽으로 이동하도록 카메라 구도를 맞추고, 바깥 마커와 바구니가 모두 보이도록 한다. 픽셀 차이를 실제cm로 환산하지 않는다.

새 도구는 움직일 수 있는 약통 마커를 제외하고 **고정 기준판 ID0~3**만 사용한다. 이전 보고의187~189px는 ID0~7 공통 모서리 기준이므로 두 수치 차이를 정렬 개선으로 해석하지 않는다. 위치 차이는 같은 ID의 원본 모서리 거리 중앙값이며, 회전은2D 유사변환으로 추정한 영상 안의 각도다.3D 카메라 yaw/pitch 값이 아니다. 마커2개 미만이면 숫자/OK를 만들지 않는다. 화면 잘림은 마커 수로 함께 확인한다.

실측 밝기(0~255 평균): 손목137.2/학습135.7, 수직114.4/118.2, 사선103.0/106.3. 시작 후 카메라 실패0/0/3회, 최대 프레임 공백약0.043/0.045/0.040초. 사선3회는 초기 연결 때 발생했으며 이후 검증 중 정상3대 프레임을 받았다. 노출·화이트밸런스는 변경하지 않았다.

## 재생 기준 에피소드와 원본 첫 프레임

- 통합 에피소드 **93번**.
- 원본 `Supermassive111/a_to_blue_20261103_193835`의 `episode_index=1`, 사용자 회차 **2회차**, `frame_index=0`.
- task: `Put medicine bottle A into the blue basket`.
- 첫 프레임 경로:
  - `/home/jetson3/so101_vla/reports/20261115/GRASP_CAUSE_7_EPISODES/full_frame_evaluation/training_first_end_effector.png`
  - `/home/jetson3/so101_vla/reports/20261115/GRASP_CAUSE_7_EPISODES/full_frame_evaluation/training_first_ceiling_vertical.png`
  - `/home/jetson3/so101_vla/reports/20261115/GRASP_CAUSE_7_EPISODES/full_frame_evaluation/training_first_ceiling_oblique.png`

동일 바이트를 `tools/monitor_web/alignment/`에 복제했고3개 이미지 SHA256을 시작 시 검증한다. 원본 영상/데이터셋은 수정하지 않았다. 관제의 “기준 에피소드·첫 프레임 이미지 경로”를 펼치면 경로와 이미지 링크가 나온다.

## 정렬 후 모델 재평가

**NOT_RUN_WAITING_ALIGNMENT**. 사용자 “정렬 완료” 알림 전에는 새 모델 평가·녹화 실기 재생·live를 실행하지 않는다.

알림을 받으면 새3대 원본 RGB와 홈 state를 사용해50액션을 예측하고, 기준93번의 첫50 녹화action과 관절별 MAE·최솟값/최댓값·이동 방향을 비교한다. 사선 픽셀 차이도 다시 측정한다. state가 실시간 게시값인지 저장된 홈 기준값인지 반드시 명시한다. 지난 실행 관절값을 현재값으로 취급하지 않는다. 첫50의 팔꿈치 오차가 기존 오프라인 수준약2°에 가깝고, 목표가 학습 방향으로 향하는지 보고한다. 밝기·색·약통 위치가 남으면 카메라별로 구분한다.

이전 최대 그리퍼10.81점은 **blue115의168프레임(5.6초) 닫힘 후보**에서 계산한 값이다. 이번 재생 기준 blue93의181프레임(6.03초)에서는1.06점이었다. 홈 영상의50액션은 모델 시간1.67초만 포함하므로 그리퍼 집기 시점 오차가 줄었다고 직접 비교할 수 없다. 초반 그리퍼50액션 오차는 별도로 계산하되, 집기 시점 개선은 그 시점의 새 영상/상태가 확보될 때까지 미확인으로 남긴다.

새 예측 비교 수치/통과 판정/live 명령은 아직 발급하지 않았다. 정렬 후 재평가를 통과하면0.33배·180초 live 명령을 제공한다. 속도·온도·상태·통신 보호나 모델 설정은 바꾸지 않았다.

## 실행·검증·변경

- 전용 카메라 미리보기 `so101-camera-alignment.service`를 user service로 시작했다. serial 열기를 audit hook으로 차단하며 로봇 객체를 생성/연결하지 않는다.
- 기존 YOLO 카메라 미리보기는 정지하고 Astra 브리지를 시작했다. LeLab backend는 유지했고 텔레옵/record/inference는 시작하지 않았다.
- `live/run.sh`는 카메라를 열기 전에 정렬 미리보기를 종료하고, 정상/오류 종료 후 원래 켜져 있었으면 복구한다. 이를 실제서비스 대신 가짜 명령으로 정상종료/실패2경로에서 검증했다.
- Windows 새 검사6개 PASS, Linux 전용 서비스 lease1개 SKIPPED. Jetson은7개 모두 PASS(lease 내부 정상/실패2경로 포함). JS2개 문법·bash 문법 PASS.
- stale/closed 미리보기는 현재 영상으로 보내지 않는다. 지난 실행의 관절값은 “지난 실행 기록”으로 표시한다. 기존 HOLD/교정 채널은 유지했다.
- 측정 후 follower serial `fuser` 비점유 확인. 보정 SHA256 `2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e` 유지. 모델·데이터·보정·PID·가속도 변경 없음.

변경 파일: `tools/alignment_metrics.py`, `tools/camera_alignment_preview.py`, `tools/so101-camera-alignment.service`, `tools/monitor_server.py`, `tools/monitor_web/{index.html,screen.js,screen.css,alignment.js}`, `tools/monitor_web/alignment/{reference.json,training_first_*.png}`, `live/run.sh`, `sim/test_alignment.py`. 기존 파일은 `.bak_20261115_시각`으로 보존했다. 설치한 unit은 `/home/jetson3/.config/systemd/user/so101-camera-alignment.service`다.

작업 예상40분,21:26 시작 기록부터21:46 보고까지약20분. 정렬 도구/검증 DONE, 현장 정렬·그 이후 모델 재평가는 대기 중이다. 카메라 전용 미리보기는 사용자가 정렬할 수 있도록 켜 둔다.
