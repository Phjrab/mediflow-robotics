# VLA 3단계 — 원본 보존 데이터 감사·검수·선별·세션 분할

2026-10-28. **오프라인 데이터 도구 단계**다. 실제 녹화·학습·모델 추론·모터 실행은 하지 않는다.
기존 제어기, 웹, safety, calibration, 카메라 설정, 원본 ACT 데이터는 변경하지 않았다.
구현 출발점은 `vla/main`의 `d5136c0c50e454ab26bc52bbe313b05b2c5155f2`다.

## 작업 흐름과 산출물

1. `vla_sessions.py`: 새 촬영의 세션 증거를 별도 JSONL에 보존하는 내부 hook.
2. `vla_audit_dataset.py`: 구조·시간·동작 값·영상 검사, `DATASET_AUDIT.json`과 `DATA_READINESS.md`.
3. `vla_review_episode.py`: 세 카메라 대표 장면 모음과 선택적 관절 그래프, 사람 검수 CSV.
4. `vla_build_derived.py`: 기본 dry-run 제외 계획, 명시적 execute 시 새 선별본만 생성.
5. `vla_split_sessions.py`: 명시적 세션 증거만 사용하는 train/eval 분할 JSON.

보조 `vla_data.py`는 읽기 전용 파서와 체크섬, `vla_official_worker.py`는 격리된 공식 로더 작업이다.
데이터 검사는 성공 동작 판정·충돌 검증·실제 로봇 재생을 대신하지 않는다.

## 세션 기록과 카운터의 실패 처리

`--sessions`는 dataset 밖의 경로를 명시한다. 예: `data/vla_sessions.jsonl`.
`--session-id`는 같은 실제 촬영 세션을 이어갈 때 사용할 식별자다. 내부 adapter는 시작 시
`new_run()`을 호출해 시각+UUID run_id를 만들고, session_id가 없으면 해당 run만의 세션을 만든다.
CLI에는 옵션만 준비돼 있으며 **실제 recorder adapter가 없어 아직 세션 기록을 발생시키지 않는다**.
기존 에피소드에 날짜·폴더·순번을 이용한 session_id를 소급 생성하지 않는다.

각 행: schema_version, episode_index, dataset_binding(root+actual_repo_id), run_id,
session_id, counter_path, combo, 실제 task, started_at(시간대 포함), camera_config_sha256,
episode_fingerprint. fingerprint는 row와 영상 경로/구간의 지문이며 영상 전체 바이트 해시는 아니다.

내부 `commit_with_session`은 sidecar lock → counter lock → 저장 완료 검증 → sidecar 원자적
append-replace → counter 원자적 replace 순서다. 기존 sidecar 바이트는 정확한 prefix로 유지한다.
두 파일을 동시에 원자적으로 바꾸는 DB transaction은 아니다. sidecar 저장 후 counter 실패 시
불일치를 남기고 다음 실행을 **fail-closed**한다. 자동 재시도·재집계·소급 보충은 하지 않는다.
감사·분할의 `read_sessions`도 명시된 counter의 확정 revision/각 이벤트를 대조하므로
sidecar만 앞서 저장된 부분 commit을 확정 세션으로 사용하지 않는다. 경로가 이동됐거나
counter가 없는 복사본도 미확정으로 거부하며 원본 이력을 조용히 재작성하지 않는다.
stale lock이나 불일치는 원본 기록을 대조해 별도로 조정해야 한다. lock 무시 외부 writer와
전원 손실에 대한 완전한 내구성 보장은 하지 않는다.

## 감사 기준

- v3.0 metadata, fps, episode/frame 총수, data offset, frame/index 연속성.
- task 존재·공백·혼합 검사와 설정된 27개 문장 대응. 기존 task를 새 문장으로 바꾸지 않는다.
- state/action 6차원과 정확한 관절 이름·순서, 유한값, 범위, 축별 최대 변화량.
- timestamp 양수 방향 증가, 1/fps 간격. 기본 허용 오차는 간격의 20%.
- 길이 MAD 또는 IQR 이상치. 기본 factor=4, 동일 길이 자료의 scale 하한은 1 frame.
- 시작 자세 분산, 시작 상태 대비 native 값 0.1 이내인 초반 대기 프레임 수.
- jump 후보: native 값 30 초과 변화. 단위 미확인 시 **30도라고 부르지 않는다**.
- 그리퍼 조기 열림 후보: 명시적으로 degree/percent가 확인된 경우에만
  그리퍼 +10pp와 팔 변화 >1도가 함께 있는 프레임. 실패·물체 낙하를 확정하는 규칙이 아니다.
- 세 영상의 존재, 해상도, fps, episode PTS 구간. 기본 full 모드는 모든 프레임 PTS를 디코드한다.
  metadata 모드는 컨테이너만 확인하고 `VIDEO_FRAME_COUNT_NOT_RUN`을 남긴다.

대표 FAIL reason: META_SCHEMA_COUNT, JOINT_DIM_NAMES_ORDER, FRAME_COUNT,
FRAME_MISSING_OR_INDEX, EPISODE_OFFSET, TIMESTAMP_NONMONOTONIC, TIMESTAMP_INTERVAL,
TASK_MISSING_BLANK_MULTIPLE, STATE_ACTION_NONFINITE_DIM, VIDEO_FRAME_COUNT,
VIDEO_MISSING_OR_UNREADABLE, SESSION_FINGERPRINT_MISMATCH.
WARN: LEGACY_CAMERA_KEYS, UNITS_UNVERIFIED, CALIBRATION_COMPATIBILITY_UNKNOWN,
SESSION_UNKNOWN, JUMP_CANDIDATE, LENGTH_OUTLIER, DEMONSTRATION_SUCCESS_UNVERIFIED 등.

행동 성공은 모든 episode에서 UNKNOWN으로 시작한다. 따라서 구조가 정상이어도 WARN일 수 있다.
WARN을 자동으로 삭제하거나 학습 준비 완료로 간주하지 않는다.

### 단위·보정값 근거

현재 코드가 degree/percent를 쓴다는 이유만으로 과거 데이터 단위를 확정하지 않는다.
`--units` 증거 JSON은 dataset binding, joint_names, arm_unit=degree,
coordinate_system=lerobot-degrees-midpoint-4095, 기록 당시 파일·행·문구·SHA256을 요구한다.
보정값 비교에는 recording calibration_sha256와 bounds source_sha256 일치,
DEGREES/4096 및 Rotation/Pitch/Elbow/Wrist_Pitch/Wrist_Roll 순서 선언이 추가로 필요하다.
이 조건을 만족할 때만 raw midpoint 기준 360/4095 변환으로 5축 범위를 비교한다.
그리퍼 물리 범위나 충돌 없는 작업공간을 이 파일에서 추론하지 않는다.
파일/hash 일치는 선언의 무결성 검사이며 선언의 물리적 사실성까지 자동 입증하지 않는다.

기존 카메라 이름은 end_effector→wrist, ceiling_vertical→top, ceiling_oblique→oblique
**참고표만** 표시한다. 실제 파일/feature를 rename하지 않는다.

## 검수와 선별본 정책

대표 장면은 각 카메라의 시작·중간·끝 포함 3~30개 프레임이다. 선택적 그래프의 값은 native 단위다.
검수 CSV에는 dataset root+metadata hash, episode index, verdict(ok/fail/hold), reason,
reviewer, date를 append한다. 같은 dataset/episode는 마지막으로 append된 행이 유효하다.
review fail/hold와 감사 FAIL은 제외 계획에 들어간다. 사람의 ok로 구조 FAIL을 덮을 수 없다.

`--execute`는 새 외부 폴더만 허용한다. 기존 out, 원본 안/상위 out은 거부한다.
공식 v0.6.0의 `_copy_and_reindex_data`, `_copy_and_reindex_episodes_metadata`를 재사용해
row/episode/task/global index/metadata를 재구성한다. **private API이므로 버전 고정이 필요**하다.
공식 public delete_episodes는 공유 영상 일부를 제외할 때 재인코딩한다. 이 도구는 디코드된
원본 샘플의 정확한 일치를 위해 MP4 전체를 새 파일로 복사하고 원래 PTS 구간을 유지한다.
hardlink/symlink는 만들지 않는다. 제외된 episode의 사용하지 않는 영상 프레임은 새 MP4에도
남을 수 있다. 이는 학습 참조에서 제외하는 도구이지 개인정보 영상 삭제·완전 제거 도구가 아니다.

numeric action/state 통계는 남긴 모든 row로 재계산하며, 영상 통계는 남긴 episode 통계를
공식 함수로 집계한다. **train-only 통계는 아직 아니며 학습 단계에서 분할 후 다시 처리해야 한다**.
출처 provenance, 제외 사유, 원본 전체 파일 SHA256 전후 동일성, 세 카메라 start/mid/end
디코드 픽셀 checksum, frame/episode 총수, 새 감사와 공식 로더 재로딩을 검증한다.
검증 실패 시 새 부분 폴더를 보존해 분석할 수 있게 하고 학습 준비 완료로 표시하지 않는다.
원본을 수정하거나 실패한 out을 자동 삭제하지 않는다. 읽을 수 없는 원본을 자동 수리하지 않는다.

## 세션 분할

기본 seed101, eval 비율0.2. session 전체가 한쪽에만 속한다. 같은 combo의 세션이 양쪽에
최소 1개씩 오도록 고정 seed 후보를 비교하지만, 불가능하면 COMBO_NOT_STRATIFIED 경고를 낸다.
세션 미확인은 unknown으로 제외한다. 세션이 하나면 eval 없이 경고한다.
파생본은 원본 episode/session을 보존하고 현재 파생본 fingerprint도 확인한다.
세션 누수 0과 episode 중복 0을 검사한다. 이 split은 학습을 실행하지 않는다.

## 실행 예시 (저장소 루트, 원본은 읽기 전용)

먼저 현재 Python에 numpy/PyArrow/PyAV/Pillow가 있어야 한다. 이 PC 검증 Python은
`/home/USER/miniconda3/envs/lerobot-3060/bin/python`(3.12.13)이다. 자동 설치는 하지 않았다.

```bash
python3 -B scripts/vla_audit_dataset.py --root /path/to/dataset --out /path/to/new-audit --official-source /path/to/lerobot-v0.6.0
python3 -B scripts/vla_review_episode.py --root /path/to/dataset --episode 0 --out /path/to/new-review --frames 5 --plot
python3 -B scripts/vla_review_episode.py --root /path/to/dataset --episode 0 --mark hold --reason "놓기 단계 확인 필요" --reviewer reviewer --reviews /path/to/reviews.csv --mark-only
python3 -B scripts/vla_build_derived.py --root /path/to/dataset --audit /path/to/new-audit/DATASET_AUDIT.json --reviews /path/to/reviews.csv --out /path/to/new-derived --dry-run
python3 -B scripts/vla_split_sessions.py --root /path/to/dataset --sessions /path/to/sessions.jsonl --out /path/to/new-split.json
```

위 `/path/to/...`는 자리표시자다. 실제 선별 실행은 dry-run과 제외 대상을 검토한 뒤
`--execute --official-source /path/to/lerobot-v0.6.0`로 별도 요청한다. 이번에는 synthetic만 실행했다.

## 공식 로더 검증과 버전

공식 v0.6.0 source commit `30da8e687a6dfc617fcd94afc367ac7071c376ce`를 child PYTHONPATH로 사용.
설치 distribution은 기존0.6.1 그대로다. 새0.6.0 환경을 설치했다고 보고하지 않는다.
`lerobot_edit_dataset --help` exit0/307줄에서 delete_episodes, split 및 관련 옵션을 확인했다.
실제 작업은 위 버전 API를 사용하며 child에서 원본 쓰기·network·serial/video open을 금지한다.
CPU dataset loader가 sample을 읽은 것이며 정책 모델을 로드하거나 추론한 것이 아니다.

## 기존 ACT 점검 결과

| 자료 | 에피소드 | 프레임 | 이번 구조 FAIL | 공식 샘플 로더 |
|---|---:|---:|---:|---|
| A-v1 | 60 | 27,519 | 0 | PASS |
| B-v1 | 60 | 27,613 | 0 | PASS |
| C-v1 | 60 | 28,966 | 0 | PASS |
| A-v2 | 60 | 32,597 | 0 | PASS |
| trim05 | 60 | 27,564 | 0 | PASS |
| 48회 선별본 | 48 | 24,588 | 0 | PASS |

348개는 루트별 합계이며 원본과 파생본이 겹치므로 독립 촬영348회가 아니다.
모두 legacy camera/task·단위·세션·성공 검수 때문에 WARN이다. session unknown348,
학습/평가 배정0. 기존6개 압축파일 SHA256은 release manifest와 모두 일치한다.
추가 다운로드·압축 해제·실제 ACT 파생 데이터 생성은 하지 않았다.
영상은 이번에 metadata 모드+공식 첫 sample, 기존 감사의54개 샘플 검사를 구분해 기록했다.
이번에도 전체 영상 프레임 수 검사는 NOT_RUN이다. A-v1 episode0 검수 이미지/그래프만 별도 생성했다.
상세 로컬 결과는 `.local/stage3/act-audits/`, 기존 근거는 상위 workspace의
`vla_prep/review_20261028/` 및 `outputs/release_assets_20261007/assets_manifest.json`이다.

## 테스트·한계·다음 단계

VLA 전용104개 PASS, skip0(1단계27+2단계42+3단계35). 실제 shared MP4/Parquet synthetic을
공식 로더로 읽고, 중간 episode 제외/재인덱싱/픽셀 checksum/원본 동일성/세션 보존을 검증했다.
새 timestamp/누락/NaN/task/camera/영상 구간/단위/제외/ledger 부분 실패도 포함한다.

최종 전체 회귀691개 실행은 기존 `.local` URDF/현장 fixture 누락, 기존 ArUco 기준 불일치로 완전 통과하지 않는다.
수정 전 HEAD를 별도 temp에 추출해 같은 guard로 실행했으며 실패6/오류23/skip15의 ID가 동일했다.
이 legacy 문제를 숨기거나 현재 실제 제어·보정값을 고쳐 테스트를 맞추지 않았다.
일부 setUpClass 오류 때문에 전체 testsRun에서 단순히 뺄셈한 PASS 수는 보고하지 않는다.
테스트의 fake telemetry/"dry_run=false" 출력은 가짜 transport 시험이지 실물 실행이 아니다.

**실제 녹화/학습/모델 추론/로봇0, 원격push NOT_PUSHED.**
3단계 끝에서 중단한다. 4단계 모델 구성·환경·학습 및 실제 recorder adapter 검증은 별도 진행이다.
복구는3단계 commit의 revert로 코드만 되돌린다. 원본 데이터·서비스 복구는 필요 없다.
전체이력 bundle 생성은 partial clone(blob:none)의 과거객체569개 미보유로 실패했다.
대신 `.local/backups/vla-stage3-20261028-incremental.bundle`에 기준
`5f03c571e223f86f422b6eb68af94a6ff05c78db` 이후1~3단계와 문서 이력을 담는다.
**self-contained가 아니며 복구 시 위 기준commit이 있는 저장소가 필요하다.**
verify/SHA 기록은 옆 receipt JSON에 둔다. 이전stage2 bundle은 유지한다.
bundle에는 git-tracked 코드·문서 이력만 있고 dataset/model은 없다.
