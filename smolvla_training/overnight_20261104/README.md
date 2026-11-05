# 2026-11-04 무인 읽기·오프라인 준비

11월 5일 오후 최신 결과: RealSense의 어두운 초기 프레임은 수집 도구의 예열 부족으로 확인했다. 노출 설정을 바꾸지 않고 초기3초를 버린 뒤 밝기가 회복됐다. REALSENSE_EXPOSURE_FINDINGS_20261105.md와 reports/three_cameras_warmed_20261105/comparison_three_cameras.png를 본다. 천장 카메라의 구도 차이와 기존 serial 중단은 남아 있으며 live는 실행하지 않았다.

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
