# 파란 바구니 실물 투입 근거 2회와 재배치 후 투입 1회

2026-11-20의 세 실행 모두 약통이 그리퍼에서 분리되어 왼쪽 파란 바구니에 남았다. 기존 배포 SmolVLA의 실물 투입 근거로 **14:40과 15:54 두 실행을 채택**했다. **15:30은 실행 도중 사람 재배치로 제외**했다. 세 실행은 투입 장면을 사후에 골라 본 사례로, 성공률의 분모를 제공하지 않는다.

| 실행 ID | READY 기준 초와 직접 확인한 장면 | 자료 한계 |
|---|---|---|
| 20261120_144002 | +63.832 그리퍼에 들린 약통, +64.999 열린 그리퍼 아래 바구니로 떨어지는 약통, +67.999 그리퍼가 물러난 뒤 바구니에 누운 약통 | +60.53~63.83 영상 공백으로 최초 들림 순간은 불명 |
| 20261120_153047 | +56.044 약통 바로 위 사람 손, +84.902 바구니 위에서 약통을 잡은 그리퍼, +85.917 열린 그리퍼와 바구니 안 약통, +88.013 바구니에 누운 약통 | 기존 판독에 +32.98 바구니 밖 낙하 및 +51~60 사람 재배치가 기록됨. 최종 투입만 성공 |
| 20261120_155459 | +46.706 들린 약통, +51.012 열린 그리퍼와 바구니 안에 선 약통, +65.034 그리퍼가 물러난 뒤에도 같은 위치 | 이후 HOLD 기록은 투입 이후의 실행 상태 |

## 원본 대조 방법

2026-11-23에 Jetson의 `runs/20261120_<시각>/steps.jsonl`과 `summary.json`을 다시 읽었다. 세 summary의 실제 필드는 `"mode": "legacy"`, `"run_label": "VLA"`, `"assist_grasp": false`, `"speed": 0.4`, READY의 `"duration_s": 180.0`이다. 각 steps에는 모델 로딩 시작·완료 이벤트가 있고, summary에는 추론 시간이 여러 회 기록되어 있다.

| 실행 · 판정 | steps.jsonl 첫 이벤트 원문 | 추론 시간 표본 | 실행 당시 Jetson HEAD (reflog) | 당시 설정의 model_sha256 |
|---|---|---:|---|---|
| 14:40:02 · 채택 | `{"event":"startup_model_begin","unix":1795153202.3768506}` | 46 | `5a6fa7e5ac6e94c64482467b0e996e645a7c1fc5` | `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654` |
| 15:30:47 · 제외 | `{"event":"startup_model_begin","unix":1795156247.0718403}` | 82 | `06d4d64712b07b387f38e2cdd492200c5e86b339` | `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654` |
| 15:54:59 · 채택 | `{"event":"startup_model_begin","unix":1795157699.6290803}` | 32 | `39464292b11fec62f67cc0f4d8067599be9d3c13` | `b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654` |

**15:30의 개입은 실행 시작 이후다.** READY 원문 시각은 `"unix": 1795156311.543228`이다. 사선 원본 `grasp_21/ceiling_oblique/00034_+000.458.jpg`와 `00046_+001.657.jpg`에서 손가락이 약통 뚜껑에 닿는다. 이벤트의 프레임 시각에서 카메라 지연을 뺀 시점은 READY +52.813초와 +54.013초다. 첫 장면은 모델 시작 이벤트로부터 117.284초 후다. 앞선 낙하와 이후 투입 사이에 재배치가 있었다.

### 실행 명령의 출처

실행별로 보존된 전체 셸 명령행이나 argv는 찾지 못했다. 다음은 각 summary의 모드와 옵션으로 복원한 동등 명령이다. 15:30과 15:54에서는 보존된 시연 래퍼 `demo.sh`의 호출 행도 대조했다.

| 실행 | 동등 명령 · 사용자 입력 원문 미확보 |
|---|---|
| 14:40 | `bash ~/so101_vla/live/run.sh --mode legacy --live --execute --operator-present --reject-stale-home --speed 0.4 --duration 180` |
| 15:30 | `bash ~/so101_vla/live/run.sh --mode legacy --live --execute --operator-present --reject-stale-home --precollect --speed 0.4 --duration 180` |
| 15:54 | `bash ~/so101_vla/live/run.sh --mode legacy --live --execute --operator-present --reject-stale-home --precollect --speed 0.4 --duration 180 --smooth-reversals` |

보존된 래퍼 호출 행 원문과 세 커밋의 `git show <HEAD>:live/config.yaml`에 공통인 설정 행은 다음과 같다.

```bash
bash "$HOME/so101_vla/live/run.sh" --mode legacy --live --execute --operator-present --reject-stale-home --precollect --speed 0.4 --duration 180 "$@"
```

```json
  "model_sha256": "b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654",
```

세 실행 모두 VLA 정책 모드는 원본 summary에서 확정했다. SHA는 당시 HEAD의 설정값까지 확정했다. 실행별 로드 파일의 SHA와 작업 트리 상태를 직접 출력한 행은 확보되지 않아 당시 메모리에 적재된 바이트까지 독립적으로 확정할 수 없다.

### 보존 영상·로그 위치

보존 폴더는 작업공간 기준 `.local/success_plots/<시각>/`이다. 각 `meta.json`의 `frames`가 READY 기준 초, 파일명, 영상 출처를 연결한다. 위 표 순서대로 확인한 파일은 144002의 f065/f071/f086, 153047의 f050/f211/f212/f209, 155459의 f091/f076/f089이다(모두 JPG). [공개 타임라인과 원본 프레임명](../model_figures/success_runs/README.md)과 [현장 판독 기록](../../docs/smolvla/WORK_LOG.md)을 함께 참조할 수 있다.

세 실행에 각각 `trace.npz`와 `meta.json`이 남아 있다. 전송 샘플 수는 시간순 4,460 / 7,906 / 3,088이다. 첫 회차는 `MediFlow_coord_20261119/first_live_144002/first_success_originals.tar.gz` 안의 원본 summary와 steps에서 4,460건을 각각 대조했다. 다른 두 회차의 수치는 보존 메타데이터 기준이다. 첫 회차와 마지막 회차에는 각각 `first_live_pick_and_place.mp4`, `MediFlow_VLA_155459_pick_and_place.mp4`가 있고, 가운데 회차는 이벤트 JPEG와 관제 캡처로 판독했다. 작업공간에서 이름이 정확히 `EVIDENCE_3RUNS`인 별도 자료는 발견하지 못했다. 이번 검증은 로컬에 보존된 자료를 사용했다.
