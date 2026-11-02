# A → blue 본 녹화 고정 설정

2026-11-02 사용자 결정: Astra 색상 원인 조사는 중단하고 현재 경로를 고정한다. **Astra 색 불일치 알려짐, 녹화·실행 모두 같은 브리지 명령 사용.** Astra의 색이 다르다는 이유만으로 에피소드를 실패 처리하거나 제외하지 않는다.

이 파일은 노트북 ad9bc48 기반 별도 fast-track의 추가 기록이다. 접근 불가한 Linux fast-track의 기존 안내서 전체를 대체하지 않는다. Linux 연결 시 아래 고정 설정·검수 기록을 기존 안내서에 추가하고 기존 내용을 보존한다.

## 고정 녹화 프로필

- 목표: 약통 A를 파란 바구니에 투입.
- task: `Put medicine bottle A into the blue basket`
- dataset 이름: `a_blue_main` 계열의 새 데이터셋. 기존 원본 삭제·덮어쓰기 없음.
- 실제 저장 카메라 이름: `ceiling_vertical`(Astra/top), `ceiling_oblique`(RealSense/oblique), `end_effector`(USB/wrist).
- 기존 학습 정규화: ceiling_vertical→top→camera2, ceiling_oblique→oblique→camera3, end_effector→wrist→camera1. 기존 단일 rename 설정을 유지한다.
- 기존 설정 목표: RGB 640×480, 30fps. 실제 저장 파일 검수 결과와 구분한다. 관절 단위는 이 문서에서 추정하지 않는다.
- 녹화 중 브리지 재시작, 카메라/바구니/조명 위치와 카메라 설정 변경 없음. 녹화와 로봇 조작은 사용자만 수행한다.
- 1회 저장 파일에서 카메라 3개가 비어 있거나 깨지지 않았는지 먼저 확인한 후 사용자가 30~50회를 진행한다. 실패 회차도 원본을 유지하고 번호만 메모한다.

## 기존 Astra 브리지 명령 — 재현용 기록, 이번 검수에서 실행하지 않음

unit: `astra-v4l2-bridge.service`

사용자가 전달했고 기존 경로 복구 후 프로세스 인자에서도 확인된 ExecStart:

```ini
ExecStart=/bin/bash -lc 'set -o pipefail; /opt/orbbec-openni2/bin/orbbec-rgb-pipe | /usr/bin/gst-launch-1.0 -q fdsrc fd=0 ! rawvideoparse width=640 height=480 framerate=30/1 format=rgb ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=/dev/video8 sync=false'
```

- OpenNI source: RGB888 640×480@30, RGB parser, videoconvert, YUY2 loopback output.
- Astra capture: `/dev/video8`, 현재 LeLab fourcc=YUYV 경로를 유지한다.
- BGR 후보 drop-in은 사용자에 의해 제거됐고 기존 YUY2 영상 복귀를 확인했다. 해당 후보를 본 녹화에 다시 적용하지 않는다.
- 소스의 RGB 형식/stride 확인만으로 전체 카메라 색 처리의 정상성을 증명할 수는 없다. 색상 원인은 **미확인**으로 남긴다.
- 학습 시 기록된 Astra 입력과 Jetson 실행 시 Astra 입력은 같은 경로를 사용해야 한다. 물체 색 판별 성공을 보장한다는 의미는 아니다.

## 본 녹화 첫 회 검수 — 현재까지 확인한 것

대상: `Supermassive111/a_blue_main_20261102_192441` (LeLab 목록 중 최신 본 녹화).
사용자 업로드 완료 후 공개 Hub revision `7bbdea944abd1792549383c3d31cc555d637d618`의 저장 파일을 별도 검토 폴더에 다운로드했다.

LeLab의 저장 데이터 읽기 전용 dataset-info 응답:

- 1 episode, 653 frames, 30 fps (프레임 수 기준 약 21.77초).
- 카메라 키 3개: observation.images.ceiling_vertical / ceiling_oblique / end_effector.
- task 문구 일치.
- action / observation.state feature 존재.
- 최초 확인 당시 Hub 공개 목록에는 없었고 LeLab source=local이었다. 이후 사용자가 업로드했으며 현재 공개 Hub 사본을 직접 읽어 확인했다.

실제 저장 파일 검수 결과:

- LeRobot 형식 v3.0. 에피소드 0의 Parquet 행 653개, 메타데이터와 일치.
- ceiling_vertical / ceiling_oblique / end_effector MP4가 각각 653프레임 정상 디코딩됐고 모두 640×480, 30/1 fps. 에피소드별 영상 경계 프레임 수도 일치.
- state/action은 모든 행에서 각각 6차원. NaN/Inf 0, timestamp 간격 이상 0 (30fps 기준 1ms 허용).
- task 문구 일치. 프레임 길이는 약 21.77초.
- 에피소드 중간 10.9초에서 카메라별 PNG 총 3장을 저장하고 육안 검사: 빈 화면/영상 깨짐 없음. Astra의 알려진 색 불일치는 허용한다. 중간 프레임에는 LIVE 문자/점검 박스가 없다.
- 별도 노트북 출력: `reports/main_recording_20261102_1934/`에 중간 이미지 3개와 inspection.json. 원본 영상·데이터와 검수 이미지는 이 Git 커밋에 넣지 않는다.
- 판정: **영상 저장 검수 PASS**. 실제 투입 성공 여부/정책 성능/로봇 성공률 판정이 아니다. 모든 프레임을 사람이 검토했다는 의미도 아니다.
- 동일한 카메라·조명·바구니·브리지 경로와 task를 유지해 사용자 녹화를 30~50회로 확장 가능. 실패 회차는 원본을 유지하고 번호/사유를 메모한다.

파일 검수는 사용자가 올린 Hub 사본을 읽어 수행했다. 추가 Jetson 복사는 불필요하다. Hub 업로드나 녹화를 대신 실행하지 않았으며 원본 읽기만 하고 복사본과 검수 이미지만 별도 경로에 생성했다.

## 실행 경계

이번 검수는 데이터 목록/메타데이터 API 읽기, Hub 저장 파일 다운로드·디코딩, 검수 이미지/안내서 작성만 수행했다. 카메라·serial 열기, 브리지/서비스 변경, 로봇·녹화·학습·재부팅·push 없음. Linux 공식 로더 재로딩은 이번 단계에서 실행하지 않았다.
