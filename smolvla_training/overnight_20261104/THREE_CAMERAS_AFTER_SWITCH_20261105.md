# YOLO 종료·Astra 시작 후 3카메라 확인

2026년 11월 5일 오후, 사용자가 승인한 카메라 서비스 전환과 세 카메라 확인을 완료했다. **이번 작업에서 로봇과 serial에는 접근하지 않았다.** 앞선 LIGHTS_FOLLOWUP의 Astra 미기동 상태는 이번 확인으로 갱신한다.

## [ACTUAL_EXECUTED]

Jetson에서 서비스의 ExecStart/ExecStop을 먼저 읽었다. YOLO는 카메라 미리보기이고 Astra는 OpenNI RGB → GStreamer → V4L2 생산 명령임을 확인했다. 다음 두 명령만 승인 범위에서 실행했다.

```bash
sudo systemctl stop medicine-yolo-preview.service
sudo systemctl start astra-v4l2-bridge.service
```

- YOLO: inactive / dead.
- Astra: active / running, Result=success.
- LeLab은 유지했다. 녹화·원격조작·추론 상태가 모두 false임을 확인한 뒤 미리보기 GET으로 각 카메라 두 프레임씩 받았다.
- USB 카메라 두 대는 비점유일 때만 열었다. Astra는 브리지 서비스에 속한 생산 프로세스만 점유 중인 것을 확인한 뒤 loopback 미리보기를 열었다.
- 요청 종료 후 USB 카메라 비점유와 Astra 생산 프로세스만 남은 상태를 확인했다. 브리지는 종료하지 않았다.
- Windows·Jetson에서 새 테스트 `python -B -m unittest discover -s tests -p test_three_cameras.py`: 각 3개 통과, exit 0. 기존 전체 테스트는 다시 실행하지 않았다.
- Windows의 첫 테스트는 Pillow의 모듈 최상위 import 때문에 오류가 났다. 이미지 import를 실제 실행 함수 안으로 옮겨 패키지 설치 없이 해결했다. 수정 전 파일은 날짜 백업을 보존했다.

## [RESULTS]

| 데이터 키 | 모델 키 | 물리 카메라 | 수신 | 평균 밝기(0~255) | 검토 결과 |
|---|---|---|---|---:|---|
| end_effector | camera1 | 손목 USB | 640×480, RGB 변환 후 PNG, 2장 | 119.35 | 집게와 장면이 보임. 학습 시작 영상과 비교적 비슷하나 동일 구도·자세 검증은 아님 |
| ceiling_vertical | camera2 | Astra 수직 | 640×480, RGB 변환 후 PNG, 2장 | 115.18 | 검은 화면 해소. 기존 색 불일치가 남아 있고, 학습 영상과 작업판 위치·시야 차이가 보임 |
| ceiling_oblique | camera3 | RealSense 사선 | 640×480, RGB 변환 후 PNG, 2장 | 55.98 | 장면은 보이지만 학습 영상보다 어둡고 로봇·작업판의 화면 내 위치와 시야가 다름 |

30 FPS를 요청했으나 실제 전달 FPS는 미측정이다. 평균 밝기는 JPEG 미리보기의 단순 픽셀 평균으로 광량 측정값이 아니다. 학습 영상과 압축·노출 조건이 달라 수치만으로 품질 통과를 판정하지 않는다. 두 프레임이 다르다는 사실도 producer 신선도나 실기 성능을 보증하지 않는다.

현재 영상과 학습 영상의 구도 차이는 이미지로 직접 확인했다. 물리 카메라 이동, 렌즈/스트림 설정, 조명 중 어느 원인인지는 미확인이다. 카메라 위치나 노출·색 설정을 자동으로 보정하지 않았다.

### 미리보기와 정책 입력 구분

이 이미지는 LeLab의 JPEG 미리보기다. Astra의 `LIVE 000002` 글자는 server.py의 미리보기 합성으로 붙는다. 이번 이미지를 학습·정책 입력으로 사용하지 않았다. 저장 원본에 LIVE가 포함됐다는 증거로 사용하지 않는다. 모델에 넣을 관측은 기존 raw RGB와 저장된 전처리·후처리 경로를 별도로 확인해야 한다.

### RealSense 경로

과거 번호가 들어간 장치 경로는 현재 없다. 새로 확인한 경로는 다음과 같다.

```text
/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435-video-index0
```

udev에서 vendor=8086, model=0b07(D435), 위 ID_SERIAL을 확인했다. ID_SERIAL_SHORT는 조회 결과에 없었다. 이전 기기 번호와의 동일성 및 경로 변경 원인은 미확인이다. 기존 실행기·LeLab 설정·학습 매핑을 변경하지 않고 확인용 코드에서 현재 경로만 사용했다.

## [FILES]

- Jetson 결과: `~/nov_live_prep_20261104/reports/three_cameras_after_switch_20261105_1430/`.
- `THREE_CAMERAS.json`: 각 카메라 수신·점유 해제와 전후 LeLab 상태.
- `comparison_three_cameras.png`: 열은 학습 빨강/초록/파랑/현재, 행은 camera1/2/3.
- 카메라별 `_0.png`, `_1.png`: 새 미리보기 프레임 총 6장.
- 새 도구 `check_three_cameras.py`, 새 모의 테스트 `tests/test_three_cameras.py`.
- `CAMERA_PATHS_VERIFIED_20261105.json`: 이번에 확인한 입력 경로 기록. 기존 실행기에 적용한 설정 파일은 아니다.

## [NEEDS_USER / UNVERIFIED]

카메라가 보이는 상태까지 복구했다. 다음은 사람이 학습 비교 이미지를 보며 특히 천장 카메라 두 대의 구도·배치를 확인하는 단계다. 모든 관측이 정상이라는 판단이나 첫 동작 시험 통과로 사용하지 않는다.

기존 serial 중단 기록은 유지한다. 현재 토크, 모터 온도, HOME 차이, 실제 상태와 결합한 첫 action은 미확인이다. 이번 지시의 로봇·serial 금지에 따라 읽기 작업도 하지 않았다.

## 재부팅 설정

읽기 확인 결과 YOLO 미리보기는 enabled, Astra 브리지는 disabled다. 이번 stop/start는 현재 실행 상태만 바꿨으며 부팅 설정은 수정하지 않았다. 재부팅하면 YOLO 자동 시작과 충돌이 다시 발생할 수 있으므로 카메라 상태를 다시 확인해야 한다.

## [NOT_RUN]

로봇·serial 열기, 모터 읽기·쓰기, 토크 변경, 정책 추론, live, 학습, 기존 venv·LeLab 설치본·체크포인트·원본 데이터 수정, 카메라 설정 파일 변경, 서비스 enable/disable 변경, push. 전환한 카메라 두 서비스 외의 서비스는 조작하지 않았다.
