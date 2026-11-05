# 2026-11-04 무인 읽기·오프라인 준비

11월 5일 소프트웨어 후속: SOFTWARE_CONTINUATION_20261105.md가 최신 보고다. 공통 관측 설정·예열·전처리 검증·모의 controller 연결 및 관련52개 테스트를 완료했다. 이번 변경의 Jetson 배포와 새 GPU 측정은 SSH timeout으로 NOT_RUN이다. 로컬 metadata PASS나 mock PASS를 실기 준비 완료로 해석하지 않는다.

- 최종 보고: OVERNIGHT_REPORT.md
- 내일 현장 확인: TOMORROW_CHECKLIST.md
- 오프라인 수치: reports/offline_eval_v2/EVALUATION_RESULTS.md
- 실제 관측: evidence/SERIAL_STOP.json
- 테스트 결과: reports/TEST_RESULTS.json (46 통과, 장치·모델 실행 없음)

코드에 기록된 전역 중단 경로는 Jetson 배포 폴더의 SERIAL_STOP.json이다. 여기의 evidence 사본은 이력 보존용이다. 저장소를 다른 폴더에 clone했다고 중단 상태를 해제한 것으로 해석하지 않는다. 로봇 앞 확인과 새 읽기 작업 지시 없이 하드웨어 명령을 실행하지 않는다.

장치 없는 테스트만 실행하려면 이 폴더에서 다음 명령을 사용한다.

```bash
python -B -m unittest discover -s tests
```

v5_proposal은 안전 판정 제안이며 live 실행기가 아니다. 기존 v4, 체크포인트, 데이터셋, 설치 환경을 수정하지 않았다.
