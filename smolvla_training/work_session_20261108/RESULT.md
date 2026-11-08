# 50스텝 완료와 연속 작업 준비 — 2026-11-08

## 실제 완료

운영자가 실행한 작업은 WORK_SEGMENT_COMPLETE, 목표 전송50회·토크 레지스터 쓰기0회, 포트2개 닫힘·정리 오류0이다. 저장된 RESULT.json을 읽어 확인했다. 원본 중단 기록은 보존됐다.

추론1.49초, 목표 전송 주기22.98Hz였다. 처음과 마지막 관측의 차이는 어깨 회전+1.58도, 어깨 올림-0.26도, 팔꿈치-0.26도, 손목 굽힘0도, 손목 회전+0.09도, 그리퍼+1.41포인트다. 마지막 관측은 마지막 명령 직전 값이며 최종 정지 자세 측정은 아니다. 목표 보정23회가 있었다. 파지·바구니 투입 성공은 미확인이다.

근거: completed_segment/RESULT.json. SHA256:793b311068cdf7ed753ecbb025aa40f39a3a619c95966b6bb9ad399e7d5ef829.

## 다음 코드

하나의 정책과 제어 연결을 유지하고 매 구간 새 관측으로50행동을 예측한다. 최대6구간·300개 목표·작업30초이며 남은 시간이 한 구간 상한10초보다 짧으면 새 구간을 시작하지 않는다. 모델 로딩과 운영자 확인 시간은 작업 시간과 별개다. 초기 home 검사는 한 번 적용하고 정상 이동한 다음 구간에 원래 home 복귀를 요구하지 않는다. 실제 관측·보정 범위·건강 상태는 매 스텝 계속 검사한다.

현재 관절 위치 대비 목표2도, 연속 목표20도/초와 정수 변환 여유, 시작50도 미만·55도 정지, 과부하·NaN·큰 점프·카메라·추론 시간 검사는 유지한다. 보정 비율은 기존 승인 범위대로 경고로 남긴다. 그리퍼는0~100 단위다. 기존 RobotController·SDK·LeLab·모델은 수정하지 않았다.

직전50스텝 성공 해시와 마지막 목표를 검증하고 새 REARM_SESSION 및 별도 LIVE 입력을 요구한다. 원래 중단·성공·사용 기록을 삭제하지 않는다. 실패는 POST_WORK_SESSION_STOP.json에 추가한다. 작업 결과는 session_chunks.json, targets.csv, step_timings.json, RESULT.json에 남는다.

## 수행과 검증

노트북과 Jetson에서 각각 관련221개(206+15) 모의 테스트 통과, 실패·오류·제외0, 종료코드0. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'다. 신규5개는300목표, 도중 실패 기록·포트 해제, 목표 동일성,30초 예산, 정상 이동 후 home 재검사 분리를 검증한다.

Jetson bash 문법·파일 사전 확인 통과,9개 파일 해시 일치·기존6개 백업 완료. 백업은 ~/v6_process_1793887709179/software_backups_20261108/1794134816506774710이다. SESSION_APPLY.json과 SESSION_VERIFICATION.json 참조. 배포 ZIP SHA256:cb7d7e1693bbcb97037a37b34bcd0bea4d93b50fae886901d3bc0104038e0b87.

개발자는 이번에 파일 읽기·코드 수정·배포·모의 테스트만 수행했다. 실제 새 세션·장치·serial·카메라 접근·추론·학습은 NOT_RUN, push는 NOT_PUSHED다. 실제300스텝 주기·이동·파지·투입은 미확인이다. 테스트 통과나 구간 완료를 G5/G6 또는 물체 작업 성공으로 기록하지 않는다.

## 운영자 다음 명령

팔과 주변 공간의 간섭을 없애고 즉시 DC 전원을 차단할 수 있을 때 직접 실행한다.

```bash
sudo -v && sudo systemctl start astra-v4l2-bridge.service && bash ~/v6_process_1793887709179/operator_candidate/run_work_session.sh --live --execute --operator-present --supported --stop-ready
```

첫 질문에는 REARM_SESSION 한 단어, 이후 별도 질문에는 LIVE 한 단어를 입력한다. WORK_SESSION_COMPLETE는 제한된 작업 구간 완료이며 물체 투입은 사람이 확인한다. HOLD가 뜨면 반복 실행하지 않고 기록을 확인한다. 현재 시작 위치가 home 범위를 벗어나면 정상 거부할 수 있으며 자동 home 이동 기능은 없다.

Ctrl+C는 추가 목표 중단과 포트 해제이며 토크OFF를 보내지 않는다. 이미 전송된 목표까지 움직일 수 있다. 예상 밖 움직임이면 DC 전원을 즉시 끄고 팔이 처지지 않게 지지한다.

## 롤백

실행 프로세스가 없는 상태에서 위 백업의 기존6개 파일을 같은 상대 위치로 복원하고 run_work_session.sh 사용을 중지한다. 신규 파일·중단·성공·사용 기록은 삭제하지 않는다. 코드 복원은 토크나 팔 자세를 바꾸지 않는다.
