# GitHub 보존 범위 — 2026-11-18

사용자가 최종 지정한 목적지는 `Phjrab/mediflow-robotics`, 브랜치는 `vla/live-202611`이다. 기존 `main`은 그대로 두고 현행 프로젝트는 `so101_vla/` 아래 보존한다. 원격 생성·조회가 끝나기 전까지 업로드 상태는 PENDING이다.

## 현재 코드와 실행 상태

Windows의 ID 복구·기록기 프로세스 분리·1초 타이밍 계측 기준 커밋은 `c52504c2895176914bdfdd10356f5799e8bd8773`이다. `pre-raw-20261118` 태그도 이 커밋을 가리킨다. 코드 회귀177 PASS/플랫폼 전용2 SKIP, 실행기17 PASS다. 이후 백업 문서만 갱신할 수 있으므로 최종 스냅샷 출처 커밋은 업로드 manifest와 함께 대조한다.

등록한 전용 SSH 키로 Jetson 재접속에 성공했고 HEAD `6732695`, 미추적 출력 파일 `review_191851_stdout.txt`, Git remote 없음 상태를 확인했다. 이 문서 작성 시점에는 Jetson 원본 이력 추가 수집, 수정본 배포, 토크 OFF 상태의 링버퍼 켬/끔 각 60초 실제 버스 읽기, 수정본 모델·카메라 dry-run, Jetson pre-raw 태그는 아직 완료하지 않았다. 이 백업을 live 검증 완료로 해석하지 않는다. raw 모드와 `--mode` 옵션도 아직 구현하지 않았다.

## Git 커밋 이력 보존 방법

연결된 GitHub 도구로 스냅샷 커밋을 생성한다. 이 API 커밋 SHA는 Windows 원본 SHA와 다르며, 원본 이력이 GitHub의 일반 커밋 목록에 직접 연결된 것은 아니다.

원본 커밋·태그를 복구할 수 있도록 `history/20261118/`에 검증한 Git bundle의 분할 파일, SHA-256 manifest, 복원 스크립트를 함께 보존한다. 각 조각은 700,000바이트 이하이며 원본 번들의 바이트 순서를 유지한다. `pre-raw-20261118` 원본 태그는 Windows bundle 내부에 들어 있다. GitHub 원격 tag ref가 별도로 만들어졌다는 뜻은 아니다.

- Windows 기준 bundle: `c52504c`와 그 이전 이력 및 pre-raw 태그. 최종 문서 커밋을 포함한 bundle로 교체할 경우 manifest가 최종 출처다.
- 과거 overnight bundle: 미게시였던 `911f3ee33a880336dad2294a4c1e69c380e4b217`, `1c59fdba815de05d92b731756857ffc3a27486d0` 포함.
- red/green publish, blue publish bundle: 다른 저장소에만 있던 원래 브랜치·태그와 커밋 보존.

복구는 GitHub 브랜치를 내려받은 작업 폴더에서 다음처럼 수행한다. 기존 실행 폴더를 덮어쓰지 않는다.

```bash
python so101_vla/history/20261118/restore_bundles.py
git bundle verify so101_vla/history/20261118/restored/vla-live-202611.bundle
git clone -b vla/live-202611 so101_vla/history/20261118/restored/vla-live-202611.bundle recovered-so101-vla
git -C recovered-so101-vla show pre-raw-20261118 --no-patch
```

## 포함·제외

포함: 현행 실행기·도구·모의 테스트·설정, 선별한 결과 보고서, 현재 관제 기능의 참조 PNG, 원래 Git 이력과 복구 해시. 과거 Git 이력에 들어 있던 기술 문서·작은 카메라 PNG도 보존된다.

제외: 모델 가중치, 학습 데이터셋 원본, raw 실행 로그·영상·JPG, 모터 보정 원본 `so-101.json`, 비밀번호·키·토큰·로컬 가상환경. 보정·모델 파일은 경로와 기존 검증 해시만 남긴다. 모델·데이터 전체 백업이 아니다.

기존 전체 reachable Git 텍스트 및 선별 보고서를 비밀정보 패턴으로 검사했고 일치0건이었다. 바이너리 이미지의 의미나 모든 비밀 형식을 검출한다는 보증은 아니다. 검증·공개 범위와 Jetson 미완료 사항은 `GITHUB_SYNC_STATUS.md`와 최신 ID_RECOVERY 보고서를 따른다.
