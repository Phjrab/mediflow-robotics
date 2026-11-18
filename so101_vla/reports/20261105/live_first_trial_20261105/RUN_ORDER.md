# 다음 실행 순서

현재 **live 차단**: 반복 지연 검증 0/3, 기존 Goal_Position 6개 모두 0으로 현재 위치와 불일치.

1. 다음 명령은 계획만 출력한다. 장치·GPU·추론 접근 없음.

   ```bash
   ~/venvs/smolvla-py310/bin/python -B ~/first_trial_operator_1793885625046_final/first_trial.py
   ```

   예상: `PLAN_ONLY`. 이 결과는 live 준비 완료가 아니다.

2. 상태를 다시 읽어야 할 때만 실행한다. LeLab 종료, 관련 포트 비점유, Astra 유지가 필요하다. `sudo -v`는 읽기 전용 fuser 점유 조회용 인증이다. 시스템을 변경하지 않는다.

   ```bash
   export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
   sudo -v
   ~/venvs/smolvla-py310/bin/python -B ~/first_trial_operator_1793885625046_final/first_trial.py --inspect --out "$HOME/first_trial_status_$(date +%Y%m%d_%H%M%S)"
   ```

   `READ_ONLY_COMPLETE`는 읽기 성공이다. 별도의 `arm_ready`를 확인해야 한다. 토크ON·온도45°C 이상·통신 오류이면 serial 작업을 중단한다. 현재 `arm_ready=false`이며 자동 수정하지 않는다.

3. 반복 dry-run 3/3 및 안전한 목표 초기화 절차가 모두 확인되기 전에는 토크를 켜지 않는다. 현재 팔을 다시 움직이거나 0 원시 목표에 맞출 필요가 없다. **HOME 재정렬만으로 Goal_Position=0 문제는 해결되지 않는다.**

4. 이후 실제 첫 시험은 현장 운영자가 직접 수행한다. 최신 반복 성공 근거, 카메라 시작 이미지 확인, 모든 안전 점검, 직접 REARM/ENABLE/LIVE 입력이 필요하다. 지금은 실행할 live 명령을 제시하지 않는다.

## 중단·종료 의미

- Ctrl+C·예외·시간 초과: 추가 목표 전송을 중단하고 포트를 닫는다. 새 HOLD 목표나 토크OFF를 자동 전송하지 않는다.
- 마지막 목표/토크가 하드웨어에 남으므로 **즉시 현재 위치에서 물리적으로 멈춘다는 보장이 없다.** 예기치 않은 움직임·충돌 위험이면 현장 운영자가 즉시 전원을 차단한다.
- 팔이 처질 수 있으므로 토크 해제나 전원 차단 전에 가능한 상황에서는 지지대로 받친다. 움직이는 관절부에 손을 넣지 않는다.
- 이 코드의 `--release`는 현장 지지 및 직접 SUPPORTED 입력이 필요한 수동 절차다. 자동 비상 정지 수단이 아니다. 이번 작업에서는 실행하지 않았다.

## 복귀

원래 설치본·모델·데이터·중단 기록은 그대로다. 새 파일만 사용을 중단하면 된다. 새 controller 경로의 물리 실행은 하지 않았으므로 원복을 위해 모터 명령을 보낼 필요가 없다. 기존 v4의 live로 되돌려 실행하지 않는다.
