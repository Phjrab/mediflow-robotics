# SmolVLA 학습 인계 — A → 빨강/초록

**새 컴퓨터 Codex에는 [인계 프롬프트](CODEX_RED_GREEN_TRAINING_HANDOFF.md)를 전달하세요.**

현재 범위는 red 9회 + green 10회 = **19회 / 9,453프레임**입니다. blue는 참고 검수 자료이며 이번 기본 학습에 포함하지 않습니다. 데이터는 Hub에서 고정 revision으로 받으며 Git에는 원본 영상/가중치/토큰이 없습니다.

```bash
git clone -b codex/smolvla-handoff-20261102 https://github.com/Phjrab/mediflow-robotics.git mediflow-robotics
cd mediflow-robotics/smolvla_training
```

위 폴더에서 Codex에게 CODEX_RED_GREEN_TRAINING_HANDOFF.md를 읽고 진행하라고 요청하세요. 이 저장소를 받는 것만으로 학습·서비스·로봇이 자동 실행되지는 않습니다.

- [이번 빨강/초록 데이터 manifest](manifests/red_green_training_manifest.json)
- [전체 사용자 제외/참고 목록](manifests/excluded_episodes.json)
- [3색 데이터 점검 결과](reports/three_color_audit/DATA_CHECK.md)
- [고정 녹화 조건과 Astra 명령](reports/RECORDING_GUIDE.md)
- `so101_code/`: ad9bc48 기반 1~6단계 보관 코드. 최신 Linux fast-track 전체 사본이 아니므로 프롬프트와 PROVENANCE.json의 제한을 먼저 읽으세요.
- `tools/`: 이번 3색 저장 데이터 검수 도구. 고정 revision을 새 경로에 내려받으며 카메라/serial은 열지 않습니다.

새 GPU PC의 CUDA/실제 loader 재로딩/19회 병합/200-step 시험/20,000-step 학습은 아직 NOT_RUN입니다. 과거 다른 장소 A-v2 학습 결과를 현재 데이터 학습 결과로 쓰지 않습니다. 실제 로봇 성공 가능성도 판정하지 않았습니다.
