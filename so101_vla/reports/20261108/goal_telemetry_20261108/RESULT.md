# 목표 전송 계측 보강 — 2026-11-08

## 현장 확인 반영

운영자는 팔이 테이블에 닿아 있으나 가동에 문제는 없다고 확인했다. 외부 간섭이 없다는 운영자 관찰로 기록한다. 개발자가 접촉력이나 기구 간섭을 직접 측정한 것은 아니다. 이 답변만으로 마찰·서보 설정·목표 제한의 영향을 확정하지 않는다.

직전 실행은284개 목표 후 팔꿈치 모델 원래 목표/현재 위치 차이12.39도에서 HOLD됐다. 실제 관측 폭은 팔꿈치0.53도·손목 굽힘0.70도였으며, SDK 호출 전 요청 목표 폭은 각각2.79도·2.72도였다. 자세한 수치는 이전 work_session_hold_20261108 분석을 따른다. 기존 중단은 보존하며 사용된 run_work_session.sh 명령을 재사용하지 않는다.

## 변경

신규 goal_telemetry.py는 기존 전송 경로에서 아래 값을 구분해 기록한다.

1. 제어기가 SDK에 넘긴 요청6축 목표
2. 기존 패킷 보호가 허용한 Goal_Position 패킷의6개 정수값과 보정 후 값
3. SDK의 send_action 반환6축 목표
4. 요청/반환/정수 변환 사이의 차이와 오류 발생 상태

trial_runtime.py의 기존 PacketPermission 검사를 먼저 통과한 목표 패킷만 계측한다. 원래 SO101Follower.send_action을 한 번 호출하며 새 serial 경로나 추가 읽기·쓰기·재시도는 만들지 않았다. 정수값의 단위 환산은 기존 bus._normalize의 메모리상 보정 계산만 사용한다. 로그 변환 오류도 HOLD로 전달한다. 기존 목표값·속도·온도·과부하·시작·보정·포트·LIVE 확인 조건은 바꾸지 않았다.

first_trial_from_hold.py는 종료 시 새 goal_telemetry.json을 덮어쓰기 없이 저장한다. 도중 예외가 난 명령도 별도 ERROR 상태로 남긴다. SDK_RETURNED는 SDK 함수가 반환됐다는 뜻이며 모터의 수신·도달 확인은 아니다. 방송형 동기 쓰기의 모터 응답은 UNVERIFIED로 명시한다. 실제 추종은 같은 실행의 targets.csv 및 후속 관측과 비교해야 한다. 이번에는 새 실기 기록을 수집하지 않았다.

## 검증·배포

기존2개(trial_runtime.py, first_trial_from_hold.py)는 백업 후 갱신했고 신규2개(goal_telemetry.py, test_goal_telemetry.py)를 추가했다. 새 테스트7개는 패킷 체크섬/ID, SDK 추가 제한과 정수 변환의 구분, 중복/누락 거부, 오류 보존, 기존 전송1회만 사용, 예외 재시도0, 무권한 호출0을 확인한다.

노트북·Jetson 각각 관련228개(213+15) 모의 테스트 통과·실패0·오류0·제외0, 종료코드0이다. 명령은 python -B -m unittest discover -s <operator_candidate 또는 v6 root> -p 'test_*.py'다. Jetson에서는 ~/venvs/smolvla-py310/bin/python을 사용했다. Python3.10 문법·4개 파일 해시 일치, 기존 모든 STOP JSON 해시 불변도 확인했다.

배포 백업: ~/v6_process_1793887709179/software_backups_20261108/1794136227238561900.

배포 ZIP SHA256:736f796eced3f8b2ed61305e66df293104ecc3b219a30d87eaae9844c0c947d7.

## 수행·남은 항목

수행: 코드 수정·백업·파일 전용 SSH 배포·모의 테스트·해시 검증. 원본 데이터·체크포인트·LeLab·SDK·RobotController는 수정하지 않았다.

NOT_RUN: 개발자의 로봇·serial·카메라 접근, 실제 정책 추론, 목표·토크 쓰기, 추가 live, 모터 설정 변경, 학습. NOT_PUSHED.

남은 미확인: SDK가 실제로 얼마나 추가 제한하는지, 작은 목표에 대한 서보 추종, 마찰·서보 설정 영향, 실제 물체 작업 성공. 새 현장 시험은 기존 HOLD를 검토한 별도 일회 절차가 필요하며 이번에 재무장/재개 경로는 추가하지 않았다. 계측 코드 배포가 실기 재개 승인이나 원인 해결을 뜻하지 않는다.

## 안전한 재검증·롤백

장치를 열지 않는 신규 계측 테스트:

```bash
~/venvs/smolvla-py310/bin/python -B -m unittest discover -s ~/v6_process_1793887709179/operator_candidate -p 'test_goal_telemetry.py'
```

코드 롤백은 실행 프로세스가 없는 상태에서 위 백업의 기존2개 파일을 복원한다. 신규 계측 파일과 모든 중단·사용·실행 기록은 보존한다. 코드 롤백으로 토크나 목표를 바꾸지 않는다.
