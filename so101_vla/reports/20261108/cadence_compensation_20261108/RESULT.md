# 주기 대기 순서의 비교 계측 준비 — 2026-11-08

운영자의 읽기 계측은 READ_ONLY_TIMING_COMPLETE, 목표/토크 쓰기0, 포트 닫힘·정리 오류0이었다. 원본은 ~/control_timing_1794130259923524372/ 이며 SHA256은68b4451c3add733babd22f676be2cf012843c48bdfee77c5aa70fc67f6452023이다.

## 확인 결과

센서 snapshot 평균20.48ms, 기록 검증4.10ms, 카메라 수집이 있었던2회는10.60/9.33ms였다. 첫 회차 이후 고정 대기33.41ms가 추가됐다. 일반 회차는 약58ms가 돼 설정30Hz보다 느렸다. 읽기 계측에는 SDK 목표 전송 시간이 포함되지 않으므로 실제 live15.9Hz의 모든 비용을 대변하지 않는다.

기존 대기 순서를 유지한 기준 기록을 보존했다. 비교 경로는 같은 읽기·검증을 먼저 끝내고 이전 marker에서33.3ms가 될 때까지 남은 시간만 기다린다. 작업이 주기보다 길면 즉시 다음 marker를 남기되 몰아서 따라잡지 않는다. 기다린 뒤 관측이33.3ms보다 오래되면 HOLD한다. 모터 목표·토크 쓰기·정책 추론은 없다.

기존 live 실행기와 이동 제한은 변경하지 않았다. 실제 비교 계측 결과를 확인한 뒤 live 주기 조정을 준비한다. 이번 결과로 실제30Hz 달성이나 live 속도 개선을 주장하지 않는다.

## 구현·검증

기존 reviewed_start_stop.py와 profile_control_timing.py를 변경하고 run_control_timing_compensated.sh 및 test_compensated_timing.py를 추가했다. 새 경로는 완료된 첫 live·기준 계측의 원본 해시와 새 READ_TIMING 직접 확인·별도 단발 기록을 요구한다. 모든 기존 중단/소비 기록을 보존한다. 비교 계측 실패는 POST_COMPENSATED_TIMING_STOP.json에 보존한다.

노트북·Jetson 각각 관련200개(185+15) 모의 테스트 통과, 실패/오류/제외0, 종료코드0이다. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'이며 Jetson에서는 ~/venvs/smolvla-py310/bin/python을 썼다. 새 모의 시계 시험은30Hz·처리 초과 시 몰아보내기 금지·대기 지연 시 오래된 관측 거부를 확인한다. 기본 코드의 목표 쓰기 권한 거부도 유지된다.

최초 파일 검사에서 기준 해시에 한 글자를 더 적은 오류가 검출됐다. 원본 파일은 변경하지 않고 고정 해시를 실제 원본 해시로 수정했다. SHA256 길이 검사를 추가한 뒤200개와 TIMING_FILES_READY를 확인했다. 초기 배포와 수정 기록은 CADENCE_APPLY.json·HASH_FIX.json, 최종 코드 해시는 CADENCE_FINAL_VERIFICATION.json에 보존한다.

기존2개 백업은 ~/v6_process_1793887709179/software_backups_20261108/1794130644664439441/ 이다. 해시 수정 전 두 파일의 추가 백업명은 HASH_FIX.json에 있다. 최종 코드 패키지 SHA256은c1c886811db25408c774c8f6202449b5a302dc64bc39d6885dffbc17299933b3이다.

## 다음 운영자 명령

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_control_timing_compensated.sh --execute --operator-present
```

질문이 뜨면 READ_TIMING 한 단어만 입력한다. READ_ONLY_TIMING_COMPLETE 출력 경로를 전달하면 기준 결과와 비교한다. HOLD는 기록을 보존하고 재실행하지 않는다. 계측은 모터 목표나 토크를 변경하지 않는다. 기존 LeLab은 종료 상태를 유지한다.

실제 수행: 완료된 파일 읽기·복사, 코드 작성/백업/배포, 모의 테스트·파일 검증. NOT_RUN: 개발자의 장치/카메라/serial 접근, 추론, 추가 live·학습. NOT_PUSHED. 실제 비교 계측과 live 주기 조정·반복 시험은 아직 미실행이다.

롤백은 실행 프로세스가 없는 상태에서 CADENCE_APPLY.json의 기존2개 백업을 복원하고 비교 래퍼를 사용 중지한다. 중단·성공·소비·계측 기록은 모두 보존한다. 코드 복원으로 토크를 끄거나 모터를 이동하지 않는다.
