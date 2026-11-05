# RealSense 노출·역광 점검: 초기 프레임 문제 확인

## 결론과 정정

**앞서 RealSense가 유난히 어둡다고 판단한 직접 원인은 미리보기 시작 직후 두 프레임만 저장한 수집 방식이었다.** 설정을 바꾸지 않고 약 2초 이상 연속 수신하자 밝기와 색이 안정됐고, 3초 예열 후 독립적으로 다시 확인해도 밝기가 회복됐다. 낮이나 불 상태, 역광 설정 때문에 지속적으로 어두운 카메라라고 판단했던 부분을 정정한다.

이 정정은 오늘 오후 불이 켜진 상태의 미리보기 비교에 관한 것이다. 밤사이 세 카메라가 모두 어두웠던 결과까지 같은 원인이라고 소급 판정하지 않는다.

자동 노출과 자동 화이트밸런스가 모두 켜진 상태에서 초기 프레임 안정화 현상을 관측했다. 어느 내부 보정 항목이 밝기·색 변화에 각각 얼마나 기여했는지와 센서의 실제 적용 노출 시간은 미확인이다. 햇빛의 영향을 완전히 배제하는 통제 실험을 한 것은 아니지만, 이번 어두운 비교 사진 문제는 카메라 설정 변경 없이 재현·해결했다.

## [ACTUAL_EXECUTED]

1. Jetson에 v4l2-ctl이 없어 새 패키지를 설치하지 않았다. 설치된 Linux V4L2 헤더와 C 컴파일러로 조회 전용 도구를 만들었다.
2. `cc -std=c11 -O2 -Wall -Wextra -Werror read_realsense_controls.c -o reports/realsense_exposure_20261105/read_controls`: exit 0.
3. 검증된 RealSense 장치 경로만 O_RDONLY로 열었다. VIDIOC_QUERYCAP / QUERYCTRL / QUERYMENU / G_CTRL / G_FMT / G_PARM만 호출했다. 설정 쓰기·스트리밍 설정 ioctl은 도구에 없다.
4. LeLab의 녹화·원격조작·추론 비활성과 카메라 비점유를 확인하고, 미리보기 영상을 6초 연속 수신했다. 시작 전·수신 중·종료 후 제어값을 읽어 비교했다.
5. 주요 네 소스(server.py, camera_opencv.py, medicine_yolo_web.py, camera_preview_server.py)에서 exposure/backlight/white-balance 등 설정 코드를 검색했다. 검색 범위에서 해당 값을 쓰는 코드는 찾지 못했다. 모든 가능한 외부 도구의 부재를 증명한 것은 아니다.
6. 카메라 확인 클라이언트만 수정해 처음 3초 동안 프레임을 계속 소비하고 버린 뒤 새 두 프레임을 저장하게 했다. 단순 sleep으로 HTTP 버퍼에 초기 프레임을 쌓아 두지 않는다. LeLab 설치본·서비스·카메라 제어값은 수정하지 않았다.
7. 수정한 클라이언트로 카메라 3대를 다시 확인했다. 로봇·serial에는 접근하지 않았다.

## 읽은 설정

| 항목 | 현재 값 | 판단 |
|---|---:|---|
| Auto Exposure | 3, Aperture Priority Mode | 자동 노출 켜짐. 수동 고정 모드가 아님 |
| Backlight Compensation | 0 | 역광 보정 꺼짐, 장치 기본값과 같음 |
| White Balance, Automatic | 1 | 자동 화이트밸런스 켜짐 |
| Brightness / Contrast / Gamma | 0 / 50 / 300 | 각각 장치 기본값과 같음 |
| Gain | 64 | 드라이버 조회값. 실시간 자동 보정 중 실제 센서 적용치라고 단정하지 않음 |
| Exposure Time, Absolute | 166, flags=16 | 자동 모드에서 비활성 표시. 실제 촬영 노출 시간으로 단정하지 않음 |
| White Balance Temperature | 4600, flags=16 | 자동 모드에서 비활성 표시. 실효 화이트밸런스 값으로 단정하지 않음 |
| Power Line Frequency | 3 | 표준 enum은 Auto. 드라이버가 메뉴 0~2만 열거한 차이는 미확인, 설정은 바꾸지 않음 |
| 스트림 구성 | YUYV 640×480, frame interval 1/30 | 구성된 값이며 실제 전달 FPS 측정 결과가 아님 |

모든 시작 전·수신 중·종료 후 조회에서 current 제어값 차이는 없었다. 비활성 항목은 현재 구성에 적용되지 않는다는 Linux V4L2 설명을 따른다. [Linux 제어 설명](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/control.html), [자동 노출 모드 설명](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/ext-ctrls-camera.html), [RealSense 옵션 정의](https://github.com/realsenseai/librealsense/blob/master/include/librealsense2/h/rs_option.h).

## 연속 프레임에서 확인한 변화

아래 시간은 HTTP 수신 기준이며 센서 자체 타임스탬프가 아니다. 밝기는 RGB 픽셀 평균(0~255)이며 광량이나 물리적 노출량 측정값이 아니다.

| 수신 경과 | 수신 프레임 번호(0부터) | 평균 밝기 |
|---:|---:|---:|
| 0.640초 | 0 | 55.763 |
| 1.007초 | 1 | 55.676 |
| 1.179초 | 2 | 55.686 |
| 2.007초 | 41 | 109.614 |
| 4.009초 | 101 | 108.630 |
| 6.010초 | 161 | 108.623 |

초기 프레임과 6초 프레임을 직접 열어 같은 작업대 장면의 밝기·색 변화를 확인했다. RGB 평균은 약 2배로 늘었지만 이를 노출 시간이 두 배였다는 뜻으로 해석하지 않는다. 안정화 뒤에도 밝은 픽셀(평균 240 이상)은 약 0.34%였다. 지속적인 심한 역광을 이번 어두운 사진의 원인으로 확정할 근거는 얻지 못했다.

3초 예열 후 별도 재확인한 RealSense 평균 밝기는 **109.226**이었다. 비교에 사용한 학습 시작 영상 세 장은 **106.308~112.388** 범위다. 평균이 비슷하다고 화질·과제 성능이 같다는 뜻은 아니다.

## 수정과 검증

- `check_three_cameras.py`: `stable_jpegs()` 추가. 처음 3초 동안 수신 프레임을 계속 버린 후 두 장을 받는다.
- 기존 LeLab 유휴 검사, USB 비점유 검사, Astra 생산자 확인, 종료 시 해제 검사는 그대로 유지했다. 수정 전 파일은 `.bak_날짜`로 보존했다.
- 새 스트림 파서 테스트 3개와 예열 테스트 2개가 Windows·Jetson에서 각각 통과했다. 명령은 `python -B -m unittest discover -s tests -p test_exposure_observation.py` 및 `... -p test_preview_warmup.py`, exit 0이다. 기존 전체 테스트 재실행은 NOT_RUN이다.
- 예열 뒤 세 카메라가 모두 640×480 프레임 두 장을 받았고, USB 비점유·Astra 생산자만 점유 상태로 정리됐다.
- 실제 정책 관측 경로는 수정하거나 실행하지 않았다. 향후 첫 관측에서도 초기 영상 안정화를 확인해야 하며, 이번 미리보기 JPEG를 정책 입력으로 쓰지 않았다.

## 파일

- `reports/realsense_exposure_20261105/controls_before.json`: 최초 조회 전체.
- `reports/realsense_exposure_20261105/sequence_v1/OBSERVATION.json`: 시작 전·수신 중·종료 후 설정, 프레임별 밝기 분포.
- 같은 폴더의 `frame_00.png`~`frame_05.png`: 초기~6초 프레임.
- `reports/three_cameras_warmed_20261105/comparison_three_cameras.png`: 학습 빨강/초록/파랑과 예열 뒤 현재 영상 비교.
- `read_realsense_controls.c`, `observe_realsense_exposure.py`: 재현용 읽기 전용 도구.

Jetson에서는 위 상대 경로 앞에 `~/nov_live_prep_20261104/`를 붙인다. 컴파일한 실행 파일은 이 전용 보고 폴더에만 있으며 기존 venv·시스템 패키지에 설치하지 않았다.

## 남은 미확인·NOT_RUN

천장 카메라 구도와 학습 당시 배치 차이는 여전히 남아 있다. 학습 당시의 노출 제어값, 센서 프레임별 실효 노출·게인·화이트밸런스, 햇빛의 개별 기여는 미확인이다.

로봇·serial 열기, 모터 읽기·쓰기, 토크 변경, 정책 추론, live, 학습, 카메라 노출·역광·화이트밸런스 제어값 변경, 기존 venv·LeLab 설치본 수정, 서비스 전환·부팅 설정 변경, 원본 데이터·체크포인트 수정, push는 이번 작업에서 NOT_RUN이다. Astra active / YOLO inactive 상태와 기존 serial 중단 파일을 유지했다.
