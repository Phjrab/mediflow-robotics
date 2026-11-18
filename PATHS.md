# 원본 경로와 공통 설정

## raw 도입 전 상태로 복구

옵션 복구: `bash ~/so101_vla/live/run.sh --mode legacy --dry-run` (raw 추가 후에도 기본 모드는 legacy).
코드 복구: 작업 변경을 보존하고 실행 프로세스를 종료한 뒤 `git -C ~/so101_vla checkout pre-raw-20261118`.
태그 원본: Windows·Jetson 모두 `c52504c2895176914bdfdd10356f5799e8bd8773`; 원격 tag ref와 bundle 게시 여부는 `GITHUB_SYNC_STATUS.md`를 확인한다.

기준은 Windows `자율설계/`, Jetson `~/`다. 보정·모델·원본 데이터는 이동하지 않았다.

| 용도 | Windows | Jetson |
|---|---|---|
| 현행 코드 | `so101_vla/live/` | `~/so101_vla/live/` |
| 공통 설정 | `so101_vla/live/config.yaml` | `~/so101_vla/live/config.yaml` |
| 모델 | `.local/models/smolvla_three_color_020000_20261104/pretrained_model/` | `~/smolvla_model/pretrained_model/` |
| 보정 확인 사본 | `reports/goal_incident_20261106/source_review/so-101.json` | 원본 `~/.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json` |
| 원본 데이터 | `.local/blue_update_20261103_200703/`, `.local/recording_checks/`, `.local/three_color_checks_20261102_204203/` 보호 | `~/.cache/huggingface/lerobot/` 보호 |
| 실행 출력 | `so101_vla/runs/` | `~/so101_vla/runs/` |
| LeLab 설치 | 참고 소스는 archive | `~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/lelab/` 원위치 |
| SmolVLA 환경 | 설치하지 않음 | `~/venvs/smolvla-py310/` 원위치 |
| CUDA 라이브러리 | 해당 없음 | `~/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib` |

모델 가중치 SHA256: `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654`.

보정 SHA256: `2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e`.

Jetson 정리 전·후 및 Windows 이전 검증값·이번 측정이 일치했다. 데이터 영상 전체의 새 해시 검사를 했다는 뜻은 아니며, 원본 데이터는 이동 대상에서 제외했다.

## 서비스

| 서비스 | 실제 파일/참조 |
|---|---|
| LeLab | `~/.config/systemd/user/lelab-jetson-py310.service` → 기존 venv의 uvicorn, 포트8022 |
| 홈 공통 설정 | `~/.config/systemd/user/so101-home-config.service` → `~/so101_vla/tools/home_config_server.py`, 포트8767 |
| 홈 화면 | 기존 `~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/frontend/dist/home-joints.html` → 새 공통 설정 사용 |
| 관제 | `~/.config/systemd/user/so101-vla-monitor.service` → `~/so101_vla/tools/monitor_server.py`, 포트8766 |
| Astra | `/etc/systemd/system/astra-v4l2-bridge.service` → `/opt/orbbec-openni2/bin/orbbec-rgb-pipe`와GStreamer, 원위치 |
| YOLO | `/etc/systemd/system/medicine-yolo-preview.service` → `~/so101-medicine-bootstrap/models/...` 원위치 |

서비스·홈 HTML 이전 파일은 `~/so101_vla/_archive/20261110/service_backup/`에 보존했다. Astra는 실제 RGB 공급자라 실행 중 유지한다. 충돌하는 YOLO·다른 미리보기·LeLab 활동만 정상 종료하고 원래 서비스 상태를 복원한다.

## 카메라

| 키 | 장치 경로 | 모델 키 |
|---|---|---|
| end_effector | `/dev/v4l/by-path/platform-3610000.usb-usb-0:2.2:1.0-video-index0` | camera1 |
| ceiling_vertical | `/dev/v4l/by-path/so101-astra-loopback-video-index0` | camera2 |
| ceiling_oblique | `/dev/v4l/by-path/platform-3610000.usb-usb-0:1.1.2:1.3-video-index0` | camera3 |

Astra 경로는 물리 USB 포트명이 아니라 `/dev/video8`을 가리키는 가상 장치 고정 별칭이다. `/etc/udev/rules.d/99-so101-astra.rules`로 생성한다. 실제 손목USB와RealSense는 sysfs의 vendor/product/serial/interface를 확인했고 config에도 정체성을 저장했다.

Astra 브리지 명령은 학습 때와 같은 RGB888→YUY2 흐름이다. 학습에도 남아 있던 Astra 색 불일치를 새 색 변환으로 바꾸지 않았다.

```text
/opt/orbbec-openni2/bin/orbbec-rgb-pipe | /usr/bin/gst-launch-1.0 -q fdsrc fd=0 ! rawvideoparse width=640 height=480 framerate=30/1 format=rgb ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device=/dev/video8 sync=false
```

LeLab·기존 venv의 Python 코드는 이번 정리에서 변경하지 않았다. LeLab 정적 홈 HTML, 관제 사용자 서비스, 새 홈 설정 서비스와 Astra 별칭 규칙만 배포했다. 설치한 추가 도구는 Ubuntu 공식 패키지 tmux와v4l-utils다.
