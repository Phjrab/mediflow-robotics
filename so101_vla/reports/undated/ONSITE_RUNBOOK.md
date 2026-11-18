# 현장 상태 및 카메라 코드 조사 — 2026-10-29

## 범위와 실행 경계
- 이번 조사는 Windows 작업 폴더와 GitHub 소스 읽기만 수행했다. 원격 Linux 채팅과 Jetson에 접속하지 않았다.
- 접속 주소·계정·호스트명·비밀번호·토큰은 이 보고서와 변경안에 저장하지 않는다.
- 로봇·모터·serial·카메라 열기, USB 조작, 서비스 변경, 영상 수신, 설치, 가중치 다운로드, 추론, 학습, shadow/live: NOT_RUN.
- 저장소 코드·설정 변경, diff 적용, commit/push: 미실행. NOT_PUSHED. 7단계 미시작.
- 보고서 생성은 사용자 요청 4번에 따른다. 코드 수정은 이 보고 이후 별도 검토 대상이다.
- 아래 현장 상태는 사용자가 전달한 결과이며 이번 세션에서 실측하지 않았다. 출력 시각은 미제공이다.

## 현장 상태
| 대상 | 전달받은 상태 | 판정 및 남은 확인 |
|---|---|---|
| serial | by-id 식별자 5AE6058306, 5AE6085272 | 기존 코드 FOLLOWER_SERIAL=5AE6058306 확정. 현재 배선·실제 포트 대응은 현장 미검증. leader=5AE6085272는 현재 미확인 |
| V4L2 RGB | usb-Generic_USB_Camera_200901010001-video-index0 한 대는 capture, index1은 비영상 | wrist/top/oblique 중 어느 역할인지 미확인. Realtek이라는 이름으로 단정하지 않음 |
| Orbbec Astra | USB ID 2bc5:0401, Audio+Vendor Specific/usbfs, Video 인터페이스와 /dev/video* 없음 | USB 열거만 확인된 상태. OpenNI RGB 스트림 성공·실패는 미확인. V4L2 노드 부재만으로 고장 판정하지 않음 |
| Intel RealSense | USB 목록에 없음 | 미연결/인식 실패 원인 미확인. 프레임·SDK·장치 serial 미확인 |
| 저장소 | Jetson clone 예정이라는 사용자 전달 | Jetson clone/환경/테스트는 NOT_RUN. GitHub vla/main HEAD는 ad9bc48e378497fa2e9b1a22b1a4c0eb3425b926 확인 |
| 설치 환경 | 현장 Python·LeRobot·SDK 출력 미제공 | 설치본 검사 NOT_RUN. 공식 v0.6.0 소스 조사와 구분 |
| 카메라 역할 | 현재 물리 대응 미제공 | 모든 실제 역할 미확인. 과거 라벨을 현재 설정에 복사하지 않음 |

serial 근거: [so101_exclusive_bus.py:21](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/5f03c571e223f86f422b6eb68af94a6ff05c78db/scripts/so101_exclusive_bus.py#L21).
상수는 코드가 의도한 follower 식별자를 증명하며 이번 현장 배선 검증을 대신하지 않는다. 과거 이력에 leader 대응 기록이 있어도 최신 전달 상태의 미확인을 덮어쓰지 않는다.

## 1. 기존 카메라 경로 조사

| 경로 | 확인한 구현 | 근거 |
|---|---|---|
| Astra 직접 RGB | C++ OpenNI2 초기화 → 장치 열기 → SENSOR_COLOR → RGB888 파이프. pyorbbecsdk 직접 사용 근거는 이 경로에서 NOT_FOUND | fix/usb-recording scripts/orbbec_rgb_pipe.cpp:1,23,29,37,43,52,64; camera_preview_server.py:89–106 |
| Astra 과거 LeLab 경로 | 역사 기록에 OpenNI source → GStreamer V4L2-loopback /dev/video8. 보관된 LeLab snapshot 구현은 OpenCV CAP_V4L2로 index 8을 읽음 | docs/STATUS.md:1592,1636–1637; mediflow lerobot_training/jetson_py310_src/lelab/server.py:1102–1118 |
| RealSense 과거 preview | /dev/v4l/by-id 경로 → OpenCV CAP_V4L2. SDK pyrealsense2 직접 호출은 해당 경로에서 NOT_FOUND | fix/usb-recording scripts/camera_preview_server.py:124–132,350–360 |
| VLM 촬영 도구 | ceiling_astra/ceiling_realsense는 중계 MJPEG 입력. 문자열 입력은 OpenCV VideoCapture 기본 backend, 장치 숫자는 CAP_V4L2 | mediflow so101_vlm_capture/config/cameras.json:9–16; so101_capture/camera.py:27–34 |
| ACT 오프라인 입력 캡처 | Astra snapshot을 ceiling_vertical로 저장하고 preview index4/6을 ceiling_oblique/end_effector로 저장. URL 수신은 urllib, 영상 디코드는 PIL. 실제 로봇 시연 수집기와 구분 | mediflow lerobot_training/capture_lelab_3cam_for_offline.py:12,28–35,59,77–86 |
| 보관 OpenCV 클래스 | config.index_or_path/config.backend로 VideoCapture. /dev/video4 전용 YUYV 처리도 있음 | mediflow lerobot_training/jetson_py310_src/camera_opencv.py:105,121–125,162–179 |
| no-camera 진단 record 래퍼 | robot.cameras={}인 진단 코드이며 3카메라 수집의 근거가 아님 | fix/usb-recording scripts/run_official_cli_record_once.sh:2,68–69 |

과거 라벨 근거: camera_preview_server.py:168–169,360에는 Astra=천장 정면, RealSense D435=천장 사선으로 적혀 있다. 따라서 역사적 설계 의도는 Astra→top 후보, RealSense→oblique 후보였으나 **현재 물리 대응은 미확인**이다. 손목은 현재 Generic USB 카메라라고 단정하지 않는다.

ACT A/B/C 전체 archive가 각 수집 시점에 어떤 저장 config/SDK 버전/프로세스를 썼는지 완전한 episode별 provenance는 이번 조사에서 NOT_FOUND. 역사 상태 기록의 8/4/6과 보관 코드 경로는 확인했지만, 이를 모든 archive의 실제 수집 경로로 일반화하지 않는다. 현재 숫자 장치 인덱스 8/4/6을 재사용하거나 새 설정에 기입하지 않았다.

주요 원문:
- [Astra OpenNI RGB](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/5f03c571e223f86f422b6eb68af94a6ff05c78db/scripts/orbbec_rgb_pipe.cpp#L23)
- [기존 Astra/RealSense bridge](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/5f03c571e223f86f422b6eb68af94a6ff05c78db/scripts/camera_preview_server.py#L89)
- [역사 loopback 상태](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/ad9bc48e378497fa2e9b1a22b1a4c0eb3425b926/docs/STATUS.md#L1592)
- [보관 LeLab Astra reader](https://github.com/Phjrab/mediflow-robotics/blob/01d77aef375ae1988b49e94286524e22564473fc/lerobot_training/jetson_py310_src/lelab/server.py#L1102)
- [VLM capture 설정](https://github.com/Phjrab/mediflow-robotics/blob/01d77aef375ae1988b49e94286524e22564473fc/so101_vlm_capture/config/cameras.json#L9)
- [ACT 오프라인 캡처](https://github.com/Phjrab/mediflow-robotics/blob/01d77aef375ae1988b49e94286524e22564473fc/lerobot_training/capture_lelab_3cam_for_offline.py#L77)

## 2. 현재 VLA 스키마와 점검기 판정

기준 ad9bc48의 configs/vla_cameras.yaml은 schema_version=1, 역할별 device/width/height/fps만 가진다.
scripts/check_vla_cameras.py:
- :57–74는 정확한 필드 집합과 /dev/v4l/by-id 형식만 허용한다.
- :89–105는 video character device/sysfs로 V4L2 식별자를 검사한다.
- :134–137은 cv.VideoCapture(device, cv.CAP_V4L2)로만 프레임을 연다.
- :171–180의 capture worker도 같은 V4L2 identity를 재확인한다.
- 결론: RealSense native SDK, OpenNI/Astra pipe, MJPEG 입력을 표현·검사할 수 없다.
  RealSense가 UVC RGB/by-id를 제공하면 기존 V4L2 경로의 후보는 될 수 있으나 현재 장치가 보이지 않으므로 사용 가능 판정은 하지 않는다.
- Astra를 과거처럼 loopback으로 중계하는 방식도 현 스키마가 요구하는 실제 by-id/USB identity와 충돌할 수 있다. 숫자 index나 임의 symlink로 우회하지 않는다.
- 모델 rename_map은 별개다. wrist→camera1, top→camera2, oblique→camera3을 유지하며 물리 미확인을 이유로 순서를 바꾸지 않는다.

[현재 점검기](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/ad9bc48e378497fa2e9b1a22b1a4c0eb3425b926/scripts/check_vla_cameras.py#L57).

### 최소 변경 제안 — 미적용
CAMERA_BACKENDS_PROPOSAL.diff는 역할별 source_type/selector를 제안하는 **설정 인터페이스 diff**다.
모든 역할은 unverified/null로 남긴다. SDK 지원까지 구현한 완성 패치가 아니며, 그대로 적용하면 현 점검기는 schema 2를 거부한다.

후속 코드 변경 범위:
1. schema 1 읽기는 유지하고 schema 2에 source_type별 selector 검증을 추가한다. 미확인·중복 identity는 capture 거부.
2. opencv는 기존 canonical by-id/USB 검사를 유지한다. intelrealsense는 검증된 serial을 사용하고 SDK import/open은 명시적 capture의 bounded child에서만 수행한다.
3. Astra는 기존 OpenNI RGB 파이프 재사용 후보를 검토한다. 현재 C++는 enumerateDevices()[0]을 열므로 다중 장치에서는 고유 URI 지정 검증이 먼저 필요하다. 재사용한다는 이유만으로 안전하다고 간주하지 않는다.
4. SDK/pipe 경로도 timeout·자식 종료/회수·RGB 형식·해상도·신선도·identity 중복 검사를 같은 수준으로 갖춰야 한다.
5. 녹화 래퍼·benchmark·preflight·어댑터 등 기존 load_config 소비자를 함께 점검한다. check_vla_cameras.py만 바꾸고 완료로 보고하지 않는다.
6. 데이터 역할 이름과 vla_train.yaml rename_map은 유지한다. 실제 위치 확인 후 selector만 결합한다.
코드 변경과 장치 검사 모두 미실행이며 승인 전 적용하지 않는다.

## 3. LeRobot 0.6.0 공식 소스 — 설치본 확인과 구분

조사 소스: huggingface/lerobot v0.6.0, commit 30da8e687a6dfc617fcd94afc367ac7071c376ce.
Jetson/원격 Linux 설치본은 이번 세션에 읽지 않았다. 따라서 **설치본 지원 확인은 NOT_RUN**이다.

| 용도 | 공식 등록 type | 실제 클래스/필드 | 판정 |
|---|---|---|---|
| V4L2/OpenCV | opencv | OpenCVCameraConfig, index_or_path; backend, fourcc 등 | configuration_opencv.py:23–25,61–66 |
| RealSense native | intelrealsense | RealSenseCameraConfig, serial_number_or_name, use_rgb, use_depth | configuration_realsense.py:20–22,58–61 |
| RealSense 실행 backend | 위와 같음 | camera_realsense.py:29–32,119 pyrealsense2, extras intelrealsense | 현재 설치·실행 여부 미확인 |
| Astra/OpenNI | NOT_FOUND (내장 설정) | 공식 cameras/utils.py:30–48에 opencv/intelrealsense/reachy2_camera/zmq만 명시 | custom-device fallback은 :50–54에 있음. Astra 플러그인이 설치됐다는 뜻은 아님 |

- [OpenCV config](https://github.com/huggingface/lerobot/blob/30da8e687a6dfc617fcd94afc367ac7071c376ce/src/lerobot/cameras/opencv/configuration_opencv.py#L23)
- [RealSense config](https://github.com/huggingface/lerobot/blob/30da8e687a6dfc617fcd94afc367ac7071c376ce/src/lerobot/cameras/realsense/configuration_realsense.py#L20)
- [RealSense backend](https://github.com/huggingface/lerobot/blob/30da8e687a6dfc617fcd94afc367ac7071c376ce/src/lerobot/cameras/realsense/camera_realsense.py#L119)
- [camera factory](https://github.com/huggingface/lerobot/blob/30da8e687a6dfc617fcd94afc367ac7071c376ce/src/lerobot/cameras/utils.py#L25)

## 수행·미실행·차단 및 재개
- 수행: 공개/접근 허용 GitHub 소스 조사, pinned commit와 파일·줄 근거 정리, 현장 상태 전달 반영, 미적용 설정 diff 작성.
- 미실행: Jetson clone/conda/Python/설치본 조회/테스트/preflight, 실제 serial·USB·카메라 검사, TX/RX 재현, 녹화·추론·학습·push.
- 차단: 현재 물리 역할과 식별자, RealSense 열거, Astra backend·단일 점유·고유 identity, 실제 설치 환경이 미확인이다.
- 대기: 현장 출력 및 물리 역할 확인. 미확인 자료를 과거 기록으로 보충하지 않는다.
- 코드 조사 재개 명령(채팅): “카메라 backend 조사 이어서: 현장 출력과 역할 대응을 반영하고 설치 소스를 읽기만 확인해줘. 코드 적용·장치 열기·학습·추론·push는 하지 마.”
- 복귀 후 로컬 저장소 확인용 명령(이번 세션 NOT_RUN): git log --oneline -1. 기대 ad9bc48. clone/환경 검사는 사용자가 실행한 결과를 받아 기록한다.
- 외부 보고서만 새로 생성했다. 원격 repo의 reports/ONSITE_RUNBOOK.md에는 아직 반영하지 않았으며, 현재 파일이 전달용 초안이다.

## 출처 고정
- sorter vla/main: ad9bc48e378497fa2e9b1a22b1a4c0eb3425b926
- sorter fix/usb-recording: 5f03c571e223f86f422b6eb68af94a6ff05c78db
- mediflow main: 01d77aef375ae1988b49e94286524e22564473fc
- LeRobot v0.6.0: 30da8e687a6dfc617fcd94afc367ac7071c376ce


## 추가 조사 — 고정 loopback 경로 방안 검토 (2026-10-29)

### 저장 위치·커밋 상태 확인
- 기존 두 파일의 실제 위치는 노트북 작업 폴더 reports/다. 원격 Linux의 vla/main 저장소 안이 아니다.
- 현재 작업 폴더는 .git은 있지만 커밋이 없는 main이다. git log는 unborn branch, ad9bc48 객체 조회는 실패했다. 따라서 이 폴더를 vla/main 작업 저장소라고 보고하지 않는다.
- 현재 로컬에서 vla/main checkout은 찾지 못했다. 원격 Linux는 사용자에 의해 꺼진 상태다.
- 다운로드 금지를 유지해 새 clone/fetch로 작업 저장소를 받지 않았고, 다른 main에 대신 커밋하지 않았다.
- 요청한 “vla/main reports/로 복사 및 별도 커밋”은 BLOCKED/NOT_COMMITTED. push는 미시도, NOT_PUSHED다. 이는 자동 승인 검사 거절이 아니라 대상 checkout 부재와 현재 작업 제약 때문이다.
- 원격 작업 저장소 접근이 가능해지면 branch=vla/main, HEAD가 ad9bc48을 포함하는지 먼저 검증하고, 기존 파일이 있으면 덮어쓰지 않는다. 내용이 같으면 복사 불필요, 다르면 새 접미사 파일로 보존하고 변경 목록을 확인한 뒤 보고서만 별도 커밋한다.
- 기존 ONSITE_RUNBOOK.md 앞부분과 CAMERA_BACKENDS_PROPOSAL.diff는 보존했다. 새 비교·미적용 diff만 추가했다.

### 최신 전달 사실 — 빈 항목은 미확인 유지
| 항목 | 최신 상태 |
|---|---|
| Generic USB RGB | by-id는 usb-Generic_USB_Camera_200901010001-video-index0, Realtek 0bda:5844라는 사용자 전달. 위치 미확인 |
| /dev/video8 | by-id/by-path 없음이라는 사용자 전달. 현재 장치명·driver·loopback 여부 미확인 |
| Astra 2bc5:0401 | USB 열거, Video 인터페이스 없음. 현재 bridge 실행 상태 미확인 |
| RealSense | 최신 출력 미제공으로 현재 미확인. 이전 전달에서는 USB 미열거였음 |
| 현재 천장 배치 | 수직/사선 모두 미확인. 과거 Astra=수직, RealSense=사선 기록은 현장 배치 확인을 대신하지 않음 |
| follower/leader | 기존 코드 follower 상수 5AE6058306. leader 5AE6085272는 현재 미확인. 이번 물리 점검 없음 |

### 1. 과거 Astra 브리지 근거
| 요청 항목 | 조사 결과 | 파일·줄 |
|---|---|---|
| 서비스 이름 | astra-v4l2-bridge.service가 과거 사용된 기록 확인. 현재 설치/active 여부 미확인 | vla ad9bc48 docs/STATUS.md:1592,1636–1637,2386,2846 |
| 정확한 시작 명령·ExecStart·전체 파이프라인 | NOT_FOUND. 접근 가능한 추적 소스에서 실제 unit 원문이나 GStreamer 전체 실행 인자까지 확보하지 못함. 추측 명령을 재현 가능한 명령으로 적지 않음 | docs/STATUS.md:2846은 외부 TTY에서 unit과 modprobe 설정을 설치했다는 기록 |
| 모듈 video_nr/card_label/exclusive_caps 실제 값 | 모두 NOT_FOUND. /dev/video8 기록으로 video_nr=8이었다고 역추정하지 않음 | 과거 설정 경로 /etc/modprobe.d/astra-v4l2loopback.conf, /etc/modules-load.d/astra-v4l2loopback.conf는 STATUS.md:2846에 있으나 내용은 없음 |
| RGB source 실행 파일 | preview 코드가 /opt/orbbec-openni2/bin/orbbec-rgb-pipe를 기본값으로 사용하고 subprocess로 실행함. loopback 서비스의 ExecStart와 같은지는 미확인 | fix scripts/camera_preview_server.py:89–106,329 |
| RGB 해상도·fps·형식 | 보관 C++는 640×480, 30fps, RGB888 모드를 선택하고 없으면 실패. 이것은 현재 서비스/카메라 측정값이 아님 | fix scripts/orbbec_rgb_pipe.cpp:59–74 |
| V4L2 출력 | 역사 기록에서 OpenNI source → GStreamer /dev/video8 sink. sink caps/fps와 모듈 설정 원문은 NOT_FOUND | vla docs/STATUS.md:1592,1636,2903 |

브리지를 시작·재시작하는 것은 producer가 OpenNI로 카메라를 여는 동작이다. 이번 세션에서는 금지하며, 현장 상태와 점유를 확인한 뒤 사용자가 직접 실행해야 한다. 서비스 자동 시작/복구 기능을 점검기에 넣지 않는다.
과거 기록에는 bridge 중단, sink 소실, buffer 오류, 반복 재시작도 있다. 역사적 성공을 현재 안정성 보장으로 인용하지 않는다.

추가 근거 파일(사용자가 현장에서 읽기만 할 대상, 이번 세션 NOT_RUN):
- systemctl cat astra-v4l2-bridge.service
- /etc/modprobe.d/astra-v4l2loopback.conf
- /etc/modules-load.d/astra-v4l2loopback.conf
- 현재 sysfs video name/virtual 경로, module 목록, producer/sink 프로세스의 필요한 인자만
주소·계정·호스트명과 불필요한 전체 프로세스 원문은 보고서에 기록하지 않는다.

### 2. 방안 비교 — 적용하지 않음
다음 크기는 소스 구조를 기준으로 한 구현 규모 추정이며 실제 생성 diff나 일정 약속이 아니다.

| 항목 | A. 기존 bridge + 고정 V4L2 경로 | B. 일반 source_type/backend 스키마 |
|---|---|---|
| 범위 | Astra만 별도 identity/freshness 계약으로 기존 OpenCV 경로 재사용. RealSense가 V4L2 RGB를 제공하면 유지 | 스키마 확장과 backend dispatch. 필요하면 native RealSense/OpenNI 어댑터 추가 |
| 변경 후보 파일 | check_vla_cameras.py, loopback 증거 helper, 확인 후 vla_cameras.yaml, 기존 producer/wrapper, tests, docs | vla_cameras.yaml, checker, 새 SDK/pipe adapters, record/benchmark/preflight/config 소비자, tests, docs |
| 예상 source diff | 약 100–250줄, 4–6개 주요 파일. producer 상태 계약 범위에 따라 증가 | 약 300–650줄, 6–10개 주요 파일. 실제 SDK 지원 범위에 따라 증가 |
| 새 테스트 규모 | 약 150–300줄: alias 바꿔치기/잘못된 driver·label·major/minor·symlink, producer 종료/heartbeat 지연/동일 프레임 반복/타임스탬프 역행, USB 회귀, 미검증 시 HOLD | 약 250–500줄: A의 신선도 위험 외 schema1 호환, backend별 selector·중복 identity, SDK 없음, pipe 자식 timeout/회수, RGB·해상도, 모든 소비자의 일관성 |
| 지연 | OpenNI→pipe/GStreamer→loopback→OpenCV 버퍼가 남음. 실측 전 미확인 | direct source에서 일부 중계 제거 가능하나 개선 보장 없음. 실측 전 미확인 |
| stale/중단 | V4L2가 마지막 프레임을 반복할 수 있음. label·경로·PID 살아 있음만으로 신선도 증명 불가 | SDK도 캐시·재연결·pipe 정지 문제가 있으므로 동일한 source 신선도 계약 필요 |
| 타임스탬프 | producer의 실제 새 프레임 seq/촬영 monotonic 시각과 소비 프레임의 결합 필요. 소비시각을 촬영시각으로 취급 금지 | backend별 clock domain·촬영시각·3카메라 skew 정합 필요 |
| 현재 준비도 | 실제 module/label/bridge 상태 미확인이므로 적용 불가 | 기존 diff는 설정 인터페이스 제안뿐이며 SDK 구현·검증 아님 |

추천은 **A를 조건부 우선 검토**하는 것이다. 기존 수집 경로를 가장 적게 바꾸며 RealSense에 불필요한 SDK 전환을 강요하지 않는다. 단, 고정 경로 허용만으로 완료하지 않는다. 현재 loopback identity와 source freshness를 증명할 수 없으면 A도 HOLD다. B는 V4L2 경로를 사용할 수 없다는 현장 근거가 생기거나 명확한 SDK 요구가 있을 때 재검토한다. 어느 안도 적용하지 않았다.

### 3. 현재 고정 경로 허용 여부와 stale 한계
- check_vla_cameras.py:73–74의 정규식은 /dev/v4l/by-id/...만 허용한다. /dev/video8 및 별도 고정 alias는 불허한다.
- :89–105는 canonical video character device와 USB 부모를 확인한다. 가상 loopback에 USB identity가 있는 것처럼 검사 우회하면 안 된다.
- :134–163은 프레임 한 장을 읽고 해상도·BGR uint8·검은 화면 휴리스틱·읽기 소요시간을 확인한다.
- :163은 measured_fps=None, sensor_capture_timestamp=None이다. 프레임 age/producer sequence를 검증하는 stale 판정기는 아니다.
- stage5 어댑터의 packet timestamp 검사는 주입된 timestamp 검사다. 실제 producer와 결합된 관측 경로는 아직 없으므로 loopback 신선도 근거로 쓸 수 없다.
- v4l2loopback 공식 README의 sustain_framerate는 필요 시 프레임 복제로 fps를 유지한다. timeout도 장치 설정일 뿐 producer가 오래된 영상을 계속 쓰는 상황까지 보장하지 않는다. 현재 설치 모듈 버전과 옵션은 미확인.
  공식 근거: https://github.com/v4l2loopback/v4l2loopback#changing-the-runtime-behaviour 및 README의 ATTRIBUTES 절.

새 ASTRA_LOOPBACK_MINIMAL_PROPOSAL.diff는 **schema 표현 단계의 최소 안전 변경안**이다:
- 예시 고정 alias 한 개를 설정 문법에서 허용하는 방향만 보여준다. 실제 alias는 생성하지 않았고 어떤 역할에도 배정하지 않았다.
- USB by-id 검증은 그대로 둔다.
- loopback capture는 identity/producer-frame freshness 구현 전까지 명시적으로 HOLD 거부한다.
- 실제 Astra capture 지원을 완성한 패치가 아니다. 현재 module·label 근거를 알 수 없어 permissive한 whitelist로 안전 검사를 약화시키지 않았다.
- 기능 코드에는 적용하지 않았다. 적용 전 A의 실제 identity·producer 증거 계약과 mock tests가 먼저 필요하다.

### 4. RealSense와 대안 — 결정하지 않음
이전 preview는 scripts/camera_preview_server.py:124–132의 CAP_V4L2와 :350–360의 Intel D435 by-id 경로를 사용했다. 보관 OpenCV 클래스도 config.index_or_path/backend로 연다. 따라서 현재 RealSense RGB가 동일한 V4L2 capture/by-id로 열거된다면 기존 backend를 유지할 수 있다는 것은 조건부 판단이다. 현재 장치가 인식되거나 실제 영상이 나온다는 뜻은 아니다.

현장 대안:
1. 사용자 직접 USB3 직결·케이블·다른 포트를 한 변수씩 확인. 이번 세션 조작·조회는 NOT_RUN.
2. V4L2 RGB 경로가 확인되면 canonical identity/역할/포맷 검증 후 기존 설정 사용 검토. 숫자 video index는 과거 값을 복사하지 않음.
3. native SDK가 필요하다는 근거가 생기면 공식 type intelrealsense를 설치 소스와 실제 환경에서 재검토. 현재 다운로드/설치 금지 유지.
4. 2카메라 전환은 결정하지 않음. 기존 3카메라 데이터·감사·녹화·mapping 소비자가 달라지므로 단순 key 삭제가 아님. 일부 과거 영상의 분리·derived manifest·새 입력 특징·재학습·가림/시점 부족 평가가 필요할 수 있다. 사전학습 config의 빈 카메라 처리나 가중치 재사용 가능 여부도 실제 설치 config/processor에서 확인해야 한다. 2카메라로 3카메라 체크포인트를 그대로 실행하거나 가짜 영상을 채워 넣지 않는다.

### 수행·차단·재개
- 수행: 현장 전달 사실의 미확인 표시 유지, 과거 bridge source/기록 조사, A/B 비교, 고정 경로 및 stale 검사 한계 확인, 보고서 append 갱신, 미적용 diff 작성.
- NOT_FOUND: 실제 service ExecStart/전체 pipeline 및 video_nr/card_label/exclusive_caps 설정값.
- NOT_RUN: Jetson 조회/연결, 모듈·서비스 조작, bridge 기동, 카메라·serial 열기, 녹화·추론·학습·다운로드·shadow/live, 7단계.
- BLOCKED/NOT_COMMITTED: 대상 vla/main checkout이 로컬에 없어 보고서를 해당 저장소에 복사·커밋하지 못했다. push는 금지대로 미시도, NOT_PUSHED.
- 재개 명령(채팅): “기존 Linux vla/main 작업 폴더를 다시 연결했어. 카메라 보고서 3개만 덮어쓰기 없이 반영해 별도 커밋해줘. 장치 접근·설치·다운로드·push는 하지 마.”
