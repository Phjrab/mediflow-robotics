# 소스코드 GitHub 업로드 확인

2026-10-07 KST. 사용자는 ChatGPT 계정과 GitHub를 연동하지 말고 이 PC의 GitHub 로그인만 사용하도록 요청했다. 이 공개 업로드는 GitHub CLI 인증으로 수행했다. 새 ChatGPT 연동이나 자동 동기화는 만들지 않았다.

- 공개 저장소: https://github.com/Phjrab/mediflow-robotics
- 소스 업로드 커밋: `0188b12b9675be59cd2ead2fb193e12585ab0761`
- 커밋 링크: https://github.com/Phjrab/mediflow-robotics/commit/0188b12b9675be59cd2ead2fb193e12585ab0761
- 촬영 도구: `so101_vlm_capture/`
- VLM: `vlm_training/`
- ACT 및 Jetson 제어 패치: `lerobot_training/`
- ArUco·OpenClaw: `openclaw_aruco/`
- 접속 주소: `docs/SITES_AND_REPOSITORIES.md`

공개용 사본 364개 파일을 준비했다. 원격 기존 이력에서 fast-forward push로 349개 변경/신규 파일을 반영했으며 기존 주차별 보고서를 유지했다. 개인 홈 경로, SSH 사용자명과 대부분의 사설 IP는 공개 사본에만 예시값으로 치환했다. 사용자가 명시적으로 요청한 현장 사이트 주소는 별도 문서에 보존했다. 소스 Python 컴파일과 JSON/JSONL 문법 검사, 제어 비동작 회귀 테스트 24개를 통과했다.

대용량 촬영 자료와 모델은 별도 Releases로 업로드 진행 중이며 완료 여부를 따로 확인한다. 현장 원본 데이터, 로봇 calibration, 모델 가중치를 수정/삭제하지 않았다. 학습·추론·텔레옵·실제 로봇 동작은 실행하지 않았다.
