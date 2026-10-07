# 원본 촬영 자료와 모델 다운로드

2026-10-07 사용자 요청으로 대용량 자료를 별도 GitHub Releases 자산으로 준비한다. ChatGPT 계정에 GitHub를 연동하지 않고 이 PC의 GitHub CLI 인증만 사용한다.

대상 저장소: https://github.com/Phjrab/mediflow-robotics

다운로드 확인: https://github.com/Phjrab/mediflow-robotics/releases

현재 상태: 업로드 준비 단계. Releases에 실제 파일이 보이기 전에는 업로드 완료가 아니다.

## 준비 범위

- 이전 환경에서 가져온 원본 `so101_pilot_dataset_ready.tar.gz`.
- 현장 촬영 원본과 이미지·영상 라벨 자료 `data/`.
- A/B/C 각 시연 데이터 및 A-v2, A-v3 trim05, A-v3 48회 파생 데이터: 각각 독립 압축 묶음. 파생 데이터에도 영상 복사본이 있으므로 일부 내용은 중복된다.
- ACT A/B/C 10K, A-v2 100K, A-v3 trim05 100K의 `pretrained_model` 가중치와 실행 설정.
- A-v3 48회 모델은 준비 시점에 완성된 최신 체크포인트만 스냅샷으로 보관한다. 파일명에 실제 STEP을 적으며 100K 완료로 표시하지 않는다.
- VLM 각 최종 어댑터와 실제 선택한 action/grasp `checkpoint-47` 어댑터. 기반 Qwen 모델은 포함하지 않는다.

각 묶음은 다운로드 후 재학습 없이 구성과 모델을 확인할 수 있도록 설정·메타데이터를 포함한다. 전체 중간 optimizer 상태, 가상환경·모델 캐시·인증정보는 제외한다. 촬영 원본과 모델을 삭제하거나 변경하지 않는다.

`assets_manifest.json`에는 묶음별 원본 상대 경로, 체크포인트 STEP, 파일 크기와 SHA-256을 기록한다. 업로드 후 다운로드 파일의 SHA-256을 비교한다. 제어 코드 공개나 모델 다운로드가 실제 로봇 동작 성공을 의미하지 않는다.
