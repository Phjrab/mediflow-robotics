# 실행한 작업

Windows 위치: `C:\Users\User\Documents\ChatGPT\자율설계\so101_vla`.
Jetson 위치: `/home/jetson3/so101_vla`.

- SSH/SFTP: Tailscale100.64.203.77, HostKeyAlias192.168.0.121. 기존 인증 사용, 비밀번호 파일/로그 저장 없음.
- 기존 실행17:45의JSONL/요약/패킷 및137회 데이터 읽기. 원본 수정 없음. 최대값 결과 `training_gripper.json`, 좌우/FK 결과 `lateral_analysis.json`.
- Windows: `python -B -m unittest discover -s sim -p 'test_*.py' -v`, `python -B -m unittest stream_live.StreamingTests -v`. PYTHONPATH는 live/deps/sim/tools/analysis를 포함한다.
- Jetson: 같은 검사에 `~/venvs/smolvla-py310/bin/python -B` 사용. 기존 CUDA 라이브러리 경로를 LD_LIBRARY_PATH에 지정. venv/패키지 변경 없음.
- Jetson: `python -B analysis/replay_gripper_protection.py --run runs/20261116_174520 --out reports/20261116/GRIPPER_CAP_LATERAL/protection_replay.json`.
- Jetson: `python3 -B reports/20261116/GRIPPER_CAP_LATERAL/run_dry_validation.py`. 내부 실제 모델/카메라 경로 `analysis/dry_timing_trace.py --duration 180 --speed .33`. serial open 차단, 가짜 모터. 카메라 정렬 서비스만 일시 중지 후 복원. 관제 원래 캐시도 복원.
- 홈 설정 사용자 서비스 재시작 및 LeLab 정적 HTML 반영. 시스템 구성/보정/LeRobot 설치본 수정 없음.
- HTTP GET: LeLab8022, 홈8022/home-joints.html, 관제100.64.203.77:8766, 설정8767/config. 모두200. 처음 잘못된 포트/유닛으로 탐색했던 결과는 post_checks.json의 정정 필드에 명시했다.
- `fuser`로 팔로워 by-id 포트 비점유 확인. serial을 열어 모터를 읽지는 않았다.
- 모델 및 보정 SHA256, 배포18파일 SHA256 대조. 모든 배포 파일 일치.
- Windows/Jetson `git diff --check`, 지정18파일만 stage, 현재 작업 브랜치 로컬 commit. push없음. Jetson의 기존 무관 미추적파일 `review_191851_stdout.txt` 보존.

이번 작업에서 실제 모터 명령·Torque_Enable·토크 한계·보정·P/I/D·가속도 쓰기0. 실제 live 실행0. 복구 토크0→1은 가짜 장치에서만 실행했다. 수동 케이블/전원 조작도 요청하지 않았다.
