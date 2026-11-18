# GitHub 동기화 상태

2026-11-18: **원본 이력·pre-raw 태그 GitHub 보존 완료**.

- 대상: `Phjrab/mediflow-robotics`, 브랜치 `vla/live-202611`.
- 보존 완료 커밋: `340c57ffb3adc1beff28d52ee21b989d3b22e2cb`.
- 정상 Git 인증 후 fast-forward push. `main`은 `9089808a9b0e424baa7403a2344f94bdbeb338c9` 그대로이며 force push하지 않았다.
- 현행 소스 경로는 원격 `so101_vla/`. API 초기 게시와 원본 Git 이력 보존을 구분한다.
- 실제 원격 `pre-raw-20261118` tag object: `5c4f5fb3902cb5970995feeb193db6517f2f9691`.
- tag target: `c52504c2895176914bdfdd10356f5799e8bd8773`, Windows·Jetson 동일.
- GitHub API로 branch, tag ref, tag target을 재조회해 일치를 확인했다.

## 원본 이력

Windows `a29e8ba`, Jetson 배포 전 `6732695`, overnight `ed8537b`, red/green `356d788`, blue `2a72321` 원본이 보존 완료 커밋의 부모로 연결돼 있다. 과거 미게시 `911f3ee`·`1c59fdb`도 조상 확인을 통과했다. `archive/windows-{overnight,red-green,blue}-20261118` 브랜치도 별도로 게시했다.

Jetson에는 검증 코드 `c52504c`와 같은 pre-raw 태그를 배포했고 Linux 테스트 179개가 통과했다. Git 보존과 로봇 실기 검증은 별개다. 이후 raw 개발·실제 버스·모델 dry-run 상태는 최신 실행 보고서를 따른다.

초기 API 소스 커밋은 `0df34a4`, 객체 감사 커밋은 `ccd827c`다. 일부 분할 bundle 업로드는 자동 승인 검토가 불투명한 binary라는 이유로 거절했다. 이후 인증된 정상 Git push로 원본 객체·태그를 보존했으므로 현재 동기화를 가로막는 문제가 아니다. 분할 bundle 전체는 로컬에만 보관하며 원격에 모두 있다고 표시하지 않는다.

## 공개 범위와 무결성

현행 코드·설정·모의 테스트·선별 보고서·작은 참조 PNG와 원본 Git 이력을 포함했다. runs 대용량 원본 로그/영상/JPG, 학습 데이터 원본, 모델 가중치, 모터 보정 원본, 인증정보, 가상환경은 제외했다. 기존 Jetson Git 이력의 558,261바이트 읽기 전용 온도 진단 표본은 원본 커밋 보존을 위해 포함했다. 5개 전체 이력 감사에서 비밀 패턴 0, 금지 파일 0을 확인했다.

| 기준 파일 | SHA-256 | 검증 범위 |
|---|---|---|
| pre-raw `live/config.yaml` 작업 파일(CRLF) | `0cb647eeea9e85399b4f9094130699eb40a1e3a17113fe117a45069cf810f5cf` | Windows·Jetson 배포 파일 일치 |
| 같은 config의 Git 원본 blob(LF) | `5f453d6533d13438a3ee22f3a5232a0eafc283daa7e86f663b393e1f7001d7a7` | pre-raw·현행·GitHub blob 동일; 줄끝 변환만 다름 |
| 보정 `so-101.json` | `2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e` | 원본 미수정·Git 미포함 |
| 모델 가중치 | `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654` | 기존 검증값; 이번 가중치 재계산 아님 |

이전 목적지 `gyeyeongjo-lgtm/so101-lelab-medicine-sorter`는 당시 두 연결 계정 모두 push 권한이 없었고 변경하지 않았다. 사용자 후속 지시에 따라 Phjrab로 목적지를 변경했다.

상세 근거: `GITHUB_BACKUP.md`, `PUBLISH_RECEIPT.json`, `reports/20261118/GITHUB_BACKUP/API_SNAPSHOT_RECEIPT.json`.
