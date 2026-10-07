# 9주차 B — Jetson 배포와 카메라 안정화

## 1. Jetson CUDA 호환 환경

기존 LeLab 환경의 PyTorch가 Jetson Orin의 compute capability 8.7과 맞지 않아 정책을 GPU에서 실행하지 못했다. 기존 환경은 보존하고 Python 3.10 기반의 별도 가상환경과 Jetson용 PyTorch를 구성했다.

- Python 3.10 격리 환경 생성
- Jetson용 PyTorch 2.8.0과 torchvision 0.23.0 사용
- CUDA 행렬 연산 확인
- ACT A/B/C 모델의 `cuda:0` 로드 확인
- 세 모델의 입력 키, 텐서 유한값, SHA-256 확인
- 별도 LeLab 서비스로 운영하여 기존 환경과 충돌 방지

## 2. 3카메라 구성

| 논리 이름 | 용도 | 연결 방식 |
|---|---|---|
| `ceiling_vertical` | 전체 작업대와 A/B/C 배치 | Astra + V4L2 bridge |
| `ceiling_oblique` | 깊이감 있는 사선 작업대 관찰 | RealSense RGB |
| `end_effector` | 그리퍼와 파지 부위 관찰 | 손목 USB 카메라 |

세 카메라가 LeRobot 데이터셋에 같은 시각 입력으로 기록되도록 이름과 해상도를 고정했다.

## 3. 해결한 카메라 문제

### RealSense 녹색 화면

OpenCV가 `/dev/video4`에 MJPG를 요청했지만 실제 스트림은 YUYV로 동작해 녹색 영상이 생성됐다. packed YUYV를 명시적으로 읽고 RGB로 변환하도록 수정해 정상 영상을 확보했다.

### Astra 정지 프레임

Astra bridge 프로세스가 살아 있어도 브라우저에는 오래된 한 프레임만 표시되는 문제가 있었다. 프리뷰 중지 시 기존 `VideoCapture`를 즉시 해제하고 새 연결에서 다시 열도록 변경했다. 브라우저의 MJPEG 표시가 멈추는 경우에는 calibration 페이지에만 100ms snapshot refresh 방식을 적용했다.

### 서비스 간 장치 점유 충돌

기존 포트의 LeLab과 새 CUDA LeLab이 동시에 `/dev/video4`, `/dev/video6`, `/dev/video8`을 열어 503과 녹화 실패를 일으켰다. 실제 사용 서비스 하나만 활성화하고 legacy 서비스를 중지·비활성화했다.

### 초기 빈 프레임

카메라 시작 직후 RealSense가 빈 프레임을 반환하는 경우가 있어 최대 30회의 일시적 재시도를 추가했다. 이후 3카메라 동시 연결과 연속 프레임 변화를 확인했다.

## 4. UI와 운영 개선

- WebGL을 사용할 수 없는 브라우저에서도 teleoperation 페이지가 전체 흰 화면이 되지 않도록 3D 미리보기 fallback 추가
- calibration 페이지에 C/B/A 크기 기준의 화면 가이드 추가
- A=큰 약통/바구니, B=중간, C=작은 대상이라는 사용자 확인을 반영
- 오래된 A/C 반대 매핑 지시 폐기
- 프리뷰와 실제 학습 입력을 분리하여 calibration overlay가 데이터에 들어가지 않도록 함

## 검증 결과

- 3개 카메라가 동시에 열림
- 각 스트림에서 연속으로 서로 다른 프레임 수신
- 녹화 시 세 카메라 키가 모두 데이터셋에 저장됨
- LeLab 웹 서비스와 CUDA 정책 로드 정상
- 장치 점유 충돌 원인과 복구 절차 문서화

## 결론

카메라 이름이 보인다는 것과 모델이 실시간 정상 프레임을 받는다는 것은 다르다. 실제 프레임 변화, 색상, 포맷, 동시 장치 점유까지 확인해야 한다. 이후 실로봇 시험에서도 잘못된 카메라 프레임이 정책 실패에 직접 영향을 줄 수 있음을 확인했다.
