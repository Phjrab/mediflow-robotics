# GitHub 동기화 상태

확인일: 2026-11-18. **최종 목적지 변경 완료 / 원격 스냅샷 업로드 PENDING**.

## 최종 목적지

- 사용자 최종 지정: `Phjrab/mediflow-robotics` (public).
- 새 보존 브랜치: `vla/live-202611`. 기존 `main`과 기존 브랜치는 강제 변경하지 않는다.
- 연결 계정 `gunho7677-gif`의 `push=true`를 GitHub 도구로 다시 확인했다.
- Windows CLI는 저장 인증이 없으므로 연결된 GitHub 도구의 blob/tree/commit 경로를 사용한다.
- API로 만든 스냅샷 SHA와 Windows 원본 SHA는 구분한다. 원본 커밋·태그는 검증한 Git bundle로 함께 보존한다.
- 브랜치 생성 및 원격 파일/해시 조회가 끝나기 전까지 완료로 표시하지 않는다. 최종 원격 SHA는 업로드 완료 기록에 남긴다.

## 원본 이력과 태그

Windows의 소프트웨어 검증 기준 커밋은 `c52504c2895176914bdfdd10356f5799e8bd8773`, 당시 브랜치는 `vla/live-202611`이다. 로컬 `pre-raw-20261118` 태그의 대상도 같다. 이후 백업 문서 커밋은 실행 알고리즘 변경과 구분한다.

검증 기준은 36커밋의 독립 이력이다. 이전 저장소 `gyeyeongjo-lgtm/so101-lelab-medicine-sorter`의 `vla/main`과 공통 조상이 없고 당시 차이는 로컬36/원격197이었다. 이것은 Windows와 Jetson의 이력 차이가 아니다.

전용 SSH 키 등록 후 Jetson 접속에 성공했다. 원래 HEAD `6732695`, 미추적 출력 파일 `review_191851_stdout.txt`, Git remote 없음 상태를 확인하고 Jetson 원본을 별도 bundle로 보존했다. 이후 Windows와 같은 `c52504c` 및 pre-raw 태그를 배포했고 Linux 테스트 179개가 통과했다. 토크 OFF 상태 링버퍼 켬/끔 각 60초 실버스 읽기·실제 모델 dry-run은 아직 NOT_RUN이다. Windows bundle과 Jetson 원본 bundle은 구분한다.

과거 `911f3ee`·`1c59fdb`는 overnight Git 이력에 있고, 검증한 bundle로 보존한다. 별도 publish 이력의 `356d788`도 따로 보존한다. 분할 파일 복원과 SHA 검증은 `GITHUB_BACKUP.md` 및 `history/20261118/MANIFEST.json`을 따른다.

원격 스냅샷 커밋은 원래 authored metadata를 재현한 native push가 아니다. 원본 SHA·태그 복구는 bundle로 가능하며 GitHub 원격 tag ref 생성 여부와 구분한다.

## 이전 목적지 기록

직전 지정 저장소 `gyeyeongjo-lgtm/so101-lelab-medicine-sorter`는 두 연결 계정 모두 `push=false`였다. 그곳에는 새 브랜치·태그·파일을 올리지 않았다. main `8dd6761a3a1332791cb80db5ed7480fc675b1f9d`, vla/main `ad9bc48e378497fa2e9b1a22b1a4c0eb3425b926`는 그대로다. 사용자의 후속 지시로 현재 목적지를 Phjrab 저장소로 변경했다.

## 공개 범위와 무결성

현행 코드·설정·모의 테스트·선별 요약과 원래 Git 이력을 포함한다. runs 아래 대용량 실행 로그/영상/JPG, 학습 데이터 원본, 모델 가중치, 모터 보정 원본, 인증정보, 로컬 가상환경은 제외한다. `.gitignore`와 실제 추적 목록을 별도로 확인했다. 기존 Git 이력에 포함된 작은 카메라 PNG·기술 자료 및 Jetson의 558,261바이트 읽기 전용 온도 진단 표본은 원본 커밋 보존의 일부다.

| 항목 | SHA-256 | 확인 범위 |
|---|---|---|
| `live/config.yaml` | `0cb647eeea9e85399b4f9094130699eb40a1e3a17113fe117a45069cf810f5cf` | Windows 파일 확인; 설정 변경 없음 |
| 팔로워 보정 `so-101.json` | `2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e` | 저장소 밖 Windows 확인 사본; 원본 미수정·미포함 |
| 모델 가중치 | `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654` | 기존 검증값; 이번 가중치 재계산 아님 |

홈 기준, 그리퍼48.5점, 집기 오른쪽+6틱, speed0.33, READY 후180초 및 안전 설정은 유지했다. raw는 양쪽 태그·백업·선행 검증이 끝나기 전까지 구현하지 않는다.
