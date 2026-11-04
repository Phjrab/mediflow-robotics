# 2026-11-04 무인 읽기·오프라인 준비

첫 관절 토크 ON을 관측하여 이후 serial 작업을 모두 중단했다. 현재 카메라는 매우 어둡다. live 준비 완료나 G5/G6 통과 자료가 아니다.

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
