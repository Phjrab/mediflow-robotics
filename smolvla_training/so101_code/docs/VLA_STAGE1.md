# VLA 1단계 — 카메라 설정·무장치 검증

작성: 2026-10-28 (Asia/Seoul). 구현 범위는 카메라 설정/점검뿐이다.
녹화·학습·추론·모터 제어·서비스 변경·패키지 설치는 실행하지 않았다.

## 기준과 격리

- 원본: `gyeyeongjo-lgtm/so101-lelab-medicine-sorter`, `fix/usb-recording`,
  `5f03c571e223f86f422b6eb68af94a6ff05c78db`.
- 원격 조회 시 `vla/main` 없음. 별도 clone에서 로컬 `vla/main` 생성.
- 참고 대상으로 지정된 `Phjrab/mediflow-robotics` commit
  `01d77aef375ae1988b49e94286524e22564473fc`의 ACT/데이터를 이번 단계에서 복사/변경하지 않음.
- `medicine_sorter/robot.py`, `web.py`, 기존 ArUco/YOLO, calibration, 원본 데이터,
  모델, 제어 제한, 토크 정책을 변경하지 않음. 원격 push `NOT_PUSHED`.

## 사전 확인 a: 공식 LeRobot/CLI

[공식 v0.6.0 pyproject](https://github.com/huggingface/lerobot/blob/v0.6.0/pyproject.toml)를 확인했다.
해당 파일 blob SHA: `c319c3fc6bce2acd8e8180a61b71c64c82bb79d1`.

- Python `>=3.12`, torch `>=2.7,<2.12`.
- `smolvla` extra: transformers-dep, num2words `>=0.5.14,<0.6.0`, accelerate-dep.
- transformers-dep `>=5.4,<5.6`, accelerate-dep `>=1.14,<2.0`.
- 기존 Jetson py310 환경과 호환된다고 가정할 수 없다. 기존 환경 업그레이드 금지.
- 로컬 Python 3.12.13 / LeRobot **0.6.1**에서 실제 CLI help 실행:
  `lerobot-train --help`: exit 0, 384 lines.
  `lerobot-record --help`: exit 0, 225 lines.
  GPU 비활성/Hub offline 환경으로 help만 실행했으며 장치는 열지 않았다.
- 출력에서 train의 `--dataset.repo_id`, `--dataset.root`, `--rename_map`,
  record의 `--dataset.repo_id`, `--dataset.root`, `--dataset.single_task`를 확인했다.
  일반 help에 `--robot.cameras`, `--policy.path`는 나타나지 않았다. 지원 불가라고
  단정하지도, 확인된 실행 옵션이라고 소개하지도 않는다.
- 추가로 공식 v0.6.0 tag의 commit `30da8e687a6dfc617fcd94afc367ac7071c376ce`를
  별도 임시 폴더에 clone했다. child process의 PYTHONPATH만 그 `src`로 지정하고
  기존 Python3.12/설치 의존성을 사용했다. `lerobot.__file__`이 임시0.6.0 소스를 가리킴을 확인했다.
  `python -B -m lerobot.scripts.lerobot_train --help`: exit0, 3974 lines.
  `python -B -m lerobot.scripts.lerobot_record --help`: exit0, 1454 lines.
  train의 `--dataset.repo_id`, `--dataset.root`, `--rename_map`, `--policy.path`,
  record의 `--dataset.repo_id`, `--dataset.root`, `--dataset.single_task`,
  `--robot.cameras`를 이 **0.6.0 소스 CLI 도움말**에서 확인했다.
  설치된 distribution은 여전히0.6.1이며 설치/다운그레이드한 것이 아니다.
- 정확한0.6.0 의존성만 설치한 격리 환경과 Jetson CLI는 NOT_RUN이다. 도움말 성공은
  학습/녹화/모델 로딩 성공을 뜻하지 않는다. 이번 단계에 학습/녹화 실행 명령은 만들지 않는다.
- mediflow vendored 모델 파일과 공식 LeRobot 패키지를 섞지 않는다.

## 사전 확인 b: 카메라 이름과 모델 입력

2026-10-28에 [smolvla_base config](https://huggingface.co/lerobot/smolvla_base/blob/main/config.json)를
가중치 다운로드 없이 읽었다. main의 관측 결과이며 추후 학습 시 모델 revision을 고정해야 한다.

| 항목 | 확인 결과 |
|---|---|
| 모델 image keys | `observation.images.camera1`, `camera2`, `camera3` |
| 각 input feature shape | `[3,256,256]` |
| state/action shape | `[6]` / `[6]` |
| chunk / n_action_steps | 50 / 50 |
| resize_imgs_with_padding | `[512,512]` (feature shape와 구별) |
| 프로젝트 수집 keys | `wrist`, `top`, `oblique` |

모델 config는 camera1의 물리 역할을 알려주지 않는다. 따라서 wrist/top/oblique를
camera1/2/3으로 **임의 매핑하지 않는다**. 실제 화면으로 역할을 확인하고, 학습·검증·추론에서
동일한 rename map과 전처리 형상을 시험한 뒤 별도 단계에서 확정한다.
기존 ceiling_vertical/top, ceiling_oblique/oblique, end_effector/wrist는 후보 명칭 관계이지
현재 하드웨어 신원이 검증됐다는 뜻이 아니다. 현재 rename map은 `UNVERIFIED`다.

## 사전 확인 c: zero-episode TX/RX

[DATASET_VALIDATION](DATASET_VALIDATION.md)의 추가 분석을 참조한다.
과거 첫 follower observation 오류와 카메라 문제를 혼동하지 않는다.
이번 단계에서 진단 녹화, serial read/write, 토크 변경을 재실행하지 않았다.

## 사전 확인 d: Jetson 환경

SSH read-only 점검 시 `No route to host`, exit 255. 원격 명령은 실행되지 않았다.
RAM/디스크/JetPack/CUDA/torch 및 카메라 by-id 모두 `NOT_RUN`이다.
네트워크 변경, 재부팅, 서비스 재시작, 반복 접속은 하지 않았다.

연결 복구 후 **Jetson 터미널**에서 실행할 read-only 한 줄 명령:

```bash
free -h && df -h / && head -n 1 /etc/nv_tegra_release && dpkg-query -W nvidia-jetpack nvidia-l4t-core
```

```bash
ls -l /dev/v4l/by-id/
```

선택한 VLA Python 환경에서만 다음을 확인한다(모델 로드/장치 명령 없음).

```bash
python -B -c 'import sys,torch; print(sys.version); print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())'
```

패키지 조회 실패도 기록한다. `nvidia-jetpack` metapackage가 없다는 사실만으로 JetPack 전체가
없다고 단정하지 않는다. 현재 Jetson에 새 Python 환경을 설치하지 않았다.

## 구성과 동작

- `configs/vla_cameras.yaml`: 카메라 식별/크기/FPS의 단일 설정. 세 `device: null`은
  의도적인 미확인 상태이다. schema 유효와 연결 성공은 다르다.
- `scripts/check_vla_cameras.py`: 기본은 장치 없는 YAML 검증. `--capture`만 실제 camera open.
- `tests/test_vla_cameras.py`: unittest + mock. 의존성은 PyYAML, 테스트용 numpy.
  실제 capture에서만 cv2를 import한다. LeRobot/serial/robot import 없음.
- 기존 `scripts/camera_preview_server.py`의 OpenCV/V4L2 설정 방식을 참고하되 기존 서버
  모듈은 import하지 않는다. 해당 서버는 웹/영상 스레드 의존성이 있고 숫자 인덱스 변환과
  무한 재연결이 있어 새 계약과 다르다. 공유 서비스나 controller를 수정하지 않고 분리했다.

허용 device는 검증한 `/dev/v4l/by-id/<고유이름>`이다. 번호, `/dev/videoN`, tty, URL,
경로 탈출, 중복 YAML 키, 누락/추가 camera key, 중복 장치/USB 물리 카메라를 거부한다.
모든 장치의 metadata 검증이 끝나기 전에는 어떤 카메라도 열지 않는다.
실제 open 전 child에서 신원을 재검사하지만, OS hotplug를 잠그는 기능은 아니다.
촬영 중 USB 재연결은 하지 않는다.

Astra가 가상 V4L2 bridge만 노출하고 stable by-id가 없다면 `top`은 **BLOCKED**다.
`/dev/video8`을 대신 넣거나 임의 symlink로 검사를 통과시키지 않는다. 물리 serial과 bridge의
검증 가능한 연결 방식을 후속 승인 범위에서 정해야 한다. 현재 스크립트에 serial 자동선택은 없다.

각 카메라는 별도 child에서 1프레임만 읽고 release한다. 기본 timeout 10초, 허용 최대30초/대,
종료 정리 대기 최대1.2초/대. backend open/read 정체 시 terminate/kill한다.
설정한 세 카메라는 순차 점검하므로 동시 스트리밍·대역폭·장기 안정성 검사는 아니다.
LeLab 등이 사용하는 카메라를 자동으로 빼앗거나 서비스를 중지하지 않는다.

결과에는 실제 크기, 설정 FPS, backend 보고 FPS, host 수신시각, 읽기 경과 시간,
검은 화면 휴리스틱을 기록한다. 측정 FPS/센서 촬영시각은 1프레임에서 알 수 없어 **null**이다.
검은 화면은 uint8 채널 값의99% 이상이5 이하인 기준이다. 정상적인 어두운 장면을 오판할 수 있고,
non-black 한 장으로 실시간 갱신이나 물리 카메라 역할이 증명되는 것은 아니다.
`hardware_verified: true`는 세 장치 open+프레임 조건 통과만 뜻한다. liveness는 false다.
이미지/영상은 저장하지 않으며, JSON 출력 파일은 새 파일만 허용한다.

## 실행 명령 (저장소 루트에서)

무장치 검증, 원본 수정 없음:

```bash
python3 -B scripts/check_vla_cameras.py --validate-only
```

```bash
python3 -B -m unittest discover -s tests -p 'test_vla_cameras.py' -v
```

연결 복구·장치 역할 확인·다른 카메라 사용자와 조율 후에만, **새로운 결과 파일명**으로:

```bash
python3 -B scripts/check_vla_cameras.py --config configs/vla_cameras.yaml --capture --timeout-seconds 10 --output /tmp/vla-cameras-first-check.json
```

현재 null template로 위 capture 명령을 실행하면 exit2/NOT_RUN이고 device open은0이다.
exit0(validate): schema만 통과. exit0(capture): 한 장씩 통과.
exit1: 프레임 검사 실패. exit2: 설정/신원/출력 문제로 시작 불가.

## 미완료와 다음 단계

1단계 구현과 무장치 검증만 완료 대상이다. 실제3카메라 신원·연속FPS·Jetson환경·정확한
0.6.0 격리 설치환경은 미검증이며 설치나 정상 연결을 주장하지 않는다.
사용자가 **“다음”**이라고 승인하기 전 2단계 recorder/9조합 수집/학습/VLA 제어 연결은 시작하지 않는다.
원복은 기존 `fix/usb-recording` 작업 사본을 계속 사용하면 된다. 이 브랜치도
추후 변경만 `git revert <1단계 commit>`으로 되돌릴 수 있으며 데이터/서비스 복구 작업은 필요 없다.
