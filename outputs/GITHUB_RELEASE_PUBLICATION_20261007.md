# 대용량 자료 GitHub Releases 공개 확인

2026-10-07 KST. 사용자 승인에 따라 PC GitHub CLI 로그인만 사용했다. 이번 작업에서 ChatGPT와 GitHub를 연동하지 않았다.

- 저장소: https://github.com/Phjrab/mediflow-robotics
- 다운로드: https://github.com/Phjrab/mediflow-robotics/releases/tag/mediflow-archive-20261007
- 태그: `mediflow-archive-20261007`
- 원본 자료/모델: 15개 압축 묶음
- 자산 총수: 16개(묶음 15개 + `assets_manifest.json`)
- 검증: 모든 묶음의 로컬 SHA-256·파일 크기와 GitHub 자산 digest·크기가 일치; manifest 자체 SHA-256도 일치.
- 공개 상태: draft=false

초기86MB 데이터셋, 현장 촬영 data/, 6종 ACT 영상 데이터셋, 6종 ACT 정책, VLM 최종 및 선택 어댑터13개가 포함됐다. 파생 데이터셋은 일부 영상이 중복되므로 원본과 무작정 합쳐 학습하지 않는다.

ACT A/B/C는10K, A-v2와trim05는100K 완료 모델이다. 48회 정책은 준비 시점의75K 체크포인트 스냅샷이다. 기반 Qwen 모델, 전체 중간 optimizer 상태, 가상환경·캐시·인증정보는 제외했다. 원본 데이터나 calibration, 현장 모델을 삭제·수정하지 않았다. 이 공개 작업은 로봇 실제 실행이나 성공 검증이 아니다.
