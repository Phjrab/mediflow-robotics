# 도구

- `home-joints.html`: LeLab의6축 방송을 받아 홈 정렬과 VLA 자동 복구 필요 여부를 표시한다. 별도 serial을 열지 않는다.
- `home_config_server.py`: 공통 config의 화면용 부분을 제공한다. 장치를 열지 않는다.
- `monitor_server.py`, `monitor_web/`: 관제·현재 영상·중단 사유·기록 검토. 실행기 입력 영상은 캐시를 통해 받는다.
- `camera_check.py`: 실행기와 같은 카메라3대의 RGB 스냅샷·밝기·학습 비교 이미지. serial을 열지 않는다.
- `stop_lelab_activity.py`: 실행 전 기존 LeLab 활동의 정상 종료 요청.
- `99-so101-astra.rules`: Astra loopback의 고정 별칭.

LeLab 텔레옵 설치본은 원래 venv에 보존했다. 접근은 `http://<JETSON>:8022/`, 홈 화면은 같은 주소의`/home-joints.html`, 관제는 포트8766이다. 홈 설정은 포트8767에서 읽는다. 확인된 접속 주소·계정·비밀번호는 이 문서에 적지 않는다.

`run.sh --live`는 tmux 안에서 실행된다. `--speed`, `--duration`, `--release`를 지원한다. 관제 교정을 명시적으로 사용하려면`--web-corrections`를 추가한다. 보정/PID/가속도 쓰기 경로는 허용하지 않는다.
