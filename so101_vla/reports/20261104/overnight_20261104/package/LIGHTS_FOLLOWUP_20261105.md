# 11월 5일 조명 확인 후속 보고

불이 켜진 영상은 손목과 RealSense 두 대에서 확인했다. Astra와 실시간 관절값은 확인하지 못했다. 새 정책 추론이나 live는 실행하지 않았다. 밤사이 보고서는 당시 상태의 기록으로 그대로 보존한다.

## 실제 수행

1. SSH 접속 후 기존 카메라 수집 도구의 해시를 대조했다. LeLab 서비스가 실행 중이어서 독립 카메라 수집은 장치를 열기 전에 거부됐다.
2. 설치된 LeLab의 미리보기·상태 GET 구현을 읽었다. `server.py:1299`의 `/camera-preview/{camera_index}`와 `server.py:1143`의 추론 중 미리보기 거부, `server.py:1435` 이후 연결 정리 코드를 확인했다. 녹화·원격조작·추론 상태 조회는 로봇 명령을 호출하지 않는다.
3. LeLab 상태 GET에서 녹화·원격조작·추론이 모두 false임을 확인했다. 확인된 카메라 경로의 비점유도 OS fuser로 먼저 확인했다.
4. LeLab의 미리보기 GET으로 손목과 사선 카메라에서 각각 640×480 이미지 두 장을 받았다. HTTP 응답을 닫은 뒤 두 장치의 비점유를 확인했다. 미리보기 이미지는 JPEG를 PNG로 저장한 것이며 정책 입력으로 쓰지 않았다.
5. 기존 학습 영상의 빨강·초록·파랑 시작 프레임과 새 미리보기를 나란히 비교했다.
6. 새 미리보기 수신·상태 거부 모의 테스트 3개가 Windows와 Jetson에서 통과했다. 이번 명령은 `python -B -m unittest discover -s tests -p test_lights_preview.py`, exit 0이다. 기존 46개 전체 테스트를 다시 실행한 것은 아니다.

## 결과

| 카메라 | 모델 대응 | 현재 결과 | 평균 RGB |
|---|---|---|---|
| 손목 end_effector | camera1 | 640×480, 장면과 집게가 보임. 학습 영상과 색·구도 차이는 남아 있음 | 133.68 / 109.96 / 139.49 |
| 천장 수직 ceiling_vertical | camera2 | NOT_RUN: Astra 브리지 inactive/dead | 미측정 |
| 천장 사선 ceiling_oblique | camera3 | 640×480, 약통·바구니·로봇이 보임. 학습 영상보다 어둡고 물체 위치와 시야에 차이가 보임 | 37.19 / 71.77 / 60.66 |

두 카메라 모두 수신한 두 JPEG가 서로 달랐다. 이것만으로 producer 신선도나 영상 품질을 검증했다고 주장하지 않는다. 새 미리보기와 기존 학습 RGB는 압축·노출 조건이 달라 밝기 값을 엄밀한 광량 측정으로 사용하지 않는다.

### Astra 차단 이유

- `astra-v4l2-bridge.service`: inactive / dead.
- `medicine-yolo-preview.service`: active / running.
- YOLO 서비스의 `Conflicts`에 `astra-v4l2-bridge.service`가 포함돼 있다. 둘을 동시에 실행할 수 없는 설정임을 확인했다.
- Orbbec Astra는 USB 목록에 존재한다. 다만 브리지가 생산하지 않는 `/dev/video8`에서 정상 영상이 나올 것으로 가정하지 않았다.
- 서비스 종료·시작·재시작, 설정 변경은 이번 후속 작업에서 실행하지 않았다. 카메라 전용 서비스 전환이 남았다. 언제 어떤 동작으로 서비스 상태가 바뀌었는지는 미확인이다.

### RealSense 경로 변경

이전 경로에는 기기 번호 `236223023645`가 포함돼 있었으나 이번에는 그 경로가 존재하지 않았다. USB에서는 Intel RealSense D435가 확인됐고 다음 경로가 존재했다.

```text
/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435-video-index0
```

현재 경로를 읽어 미리보기 노드 번호를 구했으며, 학습 설정·기존 실행기·LeLab 저장 설정은 수정하지 않았다. camera3 이름 대응을 바꾸지 않았다. 장치 경로가 바뀐 이유와 같은 물리 카메라의 식별자 유지 여부는 미확인이다.

## 파일

- Jetson: `~/nov_live_prep_20261104/reports/lights_check_20261105_1425/`
- 위 폴더의 `LIGHTS_CHECK.json`: 카메라별 결과, 전후 LeLab 활동 상태, 포트 해제 결과.
- `comparison_lights.png`: 열은 학습 빨강/초록/파랑/현재, 행은 camera1/2/3. Astra 현재 칸은 NOT_RUN이다.
- `end_effector_0.png`, `end_effector_1.png`, `ceiling_oblique_0.png`, `ceiling_oblique_1.png`: 미리보기 저장본.
- 새 도구 `check_lights_via_lelab.py`와 모의 테스트 `tests/test_lights_preview.py`.

폴더 이름의 `1425`는 실행 식별자이며 정확한 촬영 시각을 뜻하지 않는다. 이번 영상 확인은 11월 5일 오후에 수행했다.

## 종료 상태와 남은 일

- follower, 손목, 현재 RealSense 경로는 OS 점유 조회 exit 1 및 stdout/stderr 없음으로 비점유를 확인했다. 직전 한 번의 sudo 인증 부족 조회는 결과로 사용하지 않고, 인증 후 엄격한 조회로 다시 확인했다.
- 기존 `SERIAL_STOP.json`은 보존돼 있다. serial을 열거나 모터 값을 다시 읽지 않았다. 현재 토크·모터 온도·HOME 차이는 미확인이다.
- 직접 띄운 카메라 수집 프로세스는 종료했다. 기존 LeLab과 YOLO 서비스는 유지했다.
- 레지스터 쓰기, ENABLE/LIVE 입력, 서비스 조작, 정책 추론, 학습, 원본 변경, push는 NOT_RUN이다.
- 다음 단계는 카메라 전용 서비스 전환으로 Astra 영상을 확보하고, 세 카메라의 구도·배치를 학습 당시와 맞추는 것이다. 로봇 앞 상태 확인 없이 serial 중단을 해제하거나 live로 넘어가지 않는다.
