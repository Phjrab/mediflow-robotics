# GitHub 보존 완료 — 2026-11-18

최종 목적지는 `Phjrab/mediflow-robotics`, 작업 브랜치는 `vla/live-202611`이다. 현행 프로젝트 파일은 원격의 `so101_vla/` 아래 있다. 기존 `main`은 `9089808a9b0e424baa7403a2344f94bdbeb338c9`로 유지했다.

## 원본 Git 이력과 태그

일반 Git 인증 후 원본 Git 객체를 push했고, 원격 보존 커밋 `340c57ffb3adc1beff28d52ee21b989d3b22e2cb`에 다음 이력을 부모로 연결했다. 단순 파일 복사에 그치지 않고 원래 SHA·커밋 그래프·태그를 보존했다.

| 이력 | 보존한 원래 HEAD |
|---|---|
| 게시한 소스·감사 스냅샷 | `ccd827cd4a1b597a2876d52bf07ea0753452cf25` |
| Windows | `a29e8ba58399d8e1a8502c3d48a9071f5b261d0c` |
| Jetson 배포 전 | `67326955761f35de1038226fd844b8e86571d33c` |
| overnight | `ed8537b83b3a52eee4562e2950eea347ed778f80` |
| red/green publish | `356d788823d7d3d4fb98fcf711fb3c042608f863` |
| blue publish | `2a72321f55effb215f2a3e144c1ff0f12ee207b8` |

`pre-raw-20261118`은 실제 GitHub tag ref로 게시했다. 원본 tag object `5c4f5fb3902cb5970995feeb193db6517f2f9691`, 대상 커밋 `c52504c2895176914bdfdd10356f5799e8bd8773`가 Windows·Jetson과 동일하다. 태그 메시지의 “push pending”은 태그를 만들 당시의 기록이므로 원본 객체를 변경하지 않았다. GitHub API로 원격 branch·tag·tag target을 다시 읽어 확인했다.

기존 미게시 커밋 `911f3ee`와 `1c59fdb`도 원격 보존 이력의 조상으로 확인했다. 별도 원격 브랜치 `archive/windows-overnight-20261118`, `archive/windows-red-green-20261118`, `archive/windows-blue-20261118`도 남겼다. `main`과 기존 브랜치를 강제 덮어쓰지 않았다.

## 로컬 bundle과 감사

5개 원본 Git bundle 및 700,000바이트 이하 분할 사본은 로컬 `reports/20261118/GITHUB_BACKUP/`에 유지한다. 모든 분할 조각은 SHA-256 검사 및 재조립 후 원본 바이트 일치 검사를 통과했다. 원격 `so101_vla/history/20261118/*.audit.json`에는 각 Git 객체의 경로·크기·SHA·분류와 검사 결과가 있다.

초기 GitHub API 방식에서는 대용량 binary 조각 일부가 자동 승인 검토에서 거절돼 분할 bundle 업로드를 완료하지 않았다. 이후 사용자가 일반 Git 인증을 완료했고, 감사한 원본 Git 객체와 태그를 정상 push하여 이력 보존을 완료했다. GitHub에 분할 bundle이 모두 있다는 뜻은 아니다. 복구는 정상 clone/fetch와 원격 태그를 사용하면 된다.

```bash
git clone -b vla/live-202611 https://github.com/Phjrab/mediflow-robotics.git mediflow-backup
git -C mediflow-backup show pre-raw-20261118 --no-patch
git -C mediflow-backup log --all --oneline
```

## 포함·제외와 실행 검증 범위

포함: 현행 코드·설정·테스트·선별 보고서, 원래 Git 커밋과 태그, 작은 참조 PNG. 과거 Jetson 이력의 읽기 전용 온도 진단 `reports/20261115/THERMAL_WINDOW_POLICY/fresh_read/samples.jsonl`(558,261바이트)은 원본 커밋 보존을 위해 포함했다.

제외: 모델 가중치, 학습 데이터셋 원본, runs 대용량 실행 로그·영상·JPG, 모터 보정 원본 `so-101.json`, 비밀번호·키·토큰·가상환경. 5개 전체 이력의 텍스트 commit/blob/tag 검사에서 비밀 패턴 0건, 금지 파일 0건이었다. 보정·모델은 경로와 기존 검증 해시만 기록한다.

pre-raw 기준 코드의 Windows 회귀 177 PASS/플랫폼 전용 2 SKIP, 실행기 17 PASS, Jetson Linux 179 PASS를 확인했다. 후속 raw 코드와 실제 버스·모델 dry-run 결과는 별도 최신 보고서를 따른다. 이 Git 보존 완료는 live 실기 성공을 의미하지 않는다.
