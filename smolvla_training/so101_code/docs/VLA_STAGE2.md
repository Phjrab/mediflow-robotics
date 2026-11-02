# VLA 2단계 — 작업 지시·수량 관리·녹화 준비

2026-10-28 (Asia/Seoul). 시작 HEAD `1c420cfde68f878514d5f571b5fdaf106dbefd84` 확인,
clean `vla/main`에서 구현했다. 실제 장치 실행은 이 단계의 완료 조건이 아니다.

## 구현 범위와 현재 상태

- `configs/vla_tasks.yaml`: A(큰)/B(중간)/C(작은) × red/blue/green 9조합.
  조합마다 한국어2·영어1문장, 총27문장. 기본 `language_mode: korean`.
  `mixed`이면 한 조합의 한국어2+영어1을 사용한다.
- `scripts/vla_record.py`: 최소 수량 조합 추천, 문장 선택, 명령 초안, 안내 및 남은 수량 출력.
  기본은 `--dry-run`이며 **파일 생성/수정·subprocess·serial·카메라 open·모터 명령0**.
- 녹화는 텔레옵 시연 수집을 위한 향후 연결 대상이지 자동 VLA 추론이 아니다.
- `--execute`는 현재 항상 `BLOCKED`, exit2. **실제 recorder를 호출하는 코드가 없다.**
  실제 장치가 모두 정상이어도 임의 플래그나 JSON으로 해제할 수 없다.
- stage1 카메라/기존 RobotController/web/safety/ArUco/YOLO 파일은 변경하지 않았다.
  설치·서비스 변경·학습·추론·실제 녹화·모터 동작은 `NOT_RUN`.

## 기본 A:red gate와 목표 수량

기본 선택 범위는 A:red 한 조합뿐이다. 먼저 이 실제 파이프라인을 검증해야 한다.
`--expand-nine`는 **계획 범위**만9개로 확장하며 물리 검증 성공으로 간주하지 않는다.
예를 들어 `--combo C:blue`는 `--expand-nine` 없이 거부한다.
내부 live provider의 `pipeline_a_red_verified`가 없으면 향후 확장 실행도 거부한다.

- 1차: 조합별10, 총90개. 완료 수량은 구조적 저장 확인을 통과한 episode 수다.
- 2차: `secondary_per_combo`20~30, 기본20(총180), 설정30이면 총270.
- 자동 추천: 허용된 조합의 최소count. 동률은 A:red, A:blue, A:green, B:red … C:green 순서.
- 수동: `--combo A:red` 등. 자동 추천보다 우선한다.
- `cycle`: 해당 조합의 저장완료count를 기준으로 문장 순환. 준비/실패는 순서를 진행시키지 않는다.
- `random`: seed+combo+완료count로 결정, 재시도/프로세스 재실행에서도 동일하다.
- 빈문장·문장중복·조합누락을 거부한다. 혼용은 설정파일에서만 선택한다.
- 불균형 경고: 9조합의 max(count)-min(count) **>3**. 경계3은 경고 아님.
  기본 단일조합 검증 중 경고가 나더라도 9조합 실행 승인이 된 것은 아니다.
- 위치변형 지침은 설정파일에 포함한다. 검증된 작업공간·카메라 시야를 유지하며
  좌우/앞뒤 위치와 주변 약통을 다양화한다. 카메라/보정은 임의 변경하지 않는다.

## 단일 데이터셋/카운터 구조

조합마다 데이터셋을 나누지 않는다. 새 VLA용 하나의 root와 repo_id를 쓰고,
episode마다 선택한 문장을 `--dataset.single_task`로 전달할 계획이다.

- 이름 예시: `<소유자>/medicine_sorting_vla_v1`. 자동제어 대상 변경이나 schema 변경 시 v2.
- 기본 계획용 repo_id: `local/medicine_sorting_vla_v1` (업로드용 계정명이 아님).
- 기본 새 root: `.local/vla_datasets/medicine_sorting_vla_v1`.
- 카운터: `.local/vla_counters/medicine_sorting_vla_v1.json` (dataset 밖).
- 기존 ACT/VLM 데이터셋을 덮어쓰거나 자동으로 이 카운터에 편입하지 않는다.
- 모든 카메라 데이터 키는 `observation.images.wrist/top/oblique`를 유지한다.
  모델의 camera1/2/3 rename map은 4단계에서 실제 학습 help와 화면 대응을 검증한 후 사용한다.

원본 dataset은 점검 코드가 read-only로 읽는다. root와 counter가 동일/상하위 경로이면
거부하고 symlink counter도 거부한다. 기존 dataset이 있으나 카운터가 없거나 수량/지문이
다르면 오류로 멈춘다. 손상된 카운터를0으로 초기화하거나 지워서 우회하지 않는다.

## 저장 성공 판정과 원자적 카운터

카운터 업데이트 API `commit_saved_episode`는 **향후 검증된 recorder의 내부 hook**이다.
이번 CLI에는 이 함수를 호출하거나 임의 성공 JSON을 입력하는 옵션이 없다. 임시 mock 자료로만 시험했다.

다음 조건을 모두 확인한 뒤에만 증가하도록 구현했다.

1. 자식 exit0 **그리고** 정상 완료 이벤트 **그리고** 중단 아님.
   exit0만으로 완료를 만들지 않는다. UI stop/예외 이후 finalize로 남은 부분episode는 증가 금지.
2. read-only snapshot에서 episode가 정확히1개 늘고, 전체 frame 증가량이 해당episode 길이와 일치.
3. 기존episode의 row/영상참조 지문이 동일하며 새task가 선택 문장과 일치.
4. v3 `meta/info.json`, `meta/tasks.parquet`, `meta/episodes/*.parquet`, 실제 data rows,
   wrist/top/oblique 영상 파일 존재·nonempty 확인. index·offset·단조timestamp·6차원 유한값 검사.
5. 기대 counter revision, dataset root/repo binding 및 이전 지문 일치.

mp4의 모든 프레임 디코딩/코덱 검증/행동 성공 판정까지 하는 검사는 아니다. 실제로
바구니에 넣었는지의 품질 검수와는 구분해야 한다. PyArrow는 존재하는v3 데이터를 읽을 때만
필요하고, 새 빈 root의 dry-run에는 필요 없다. 미지원 schema는 변환하지 않고 차단한다.

저장은 counter별 O_EXCL lock → 최신revision 재확인 → 임시파일 flush/fsync →
dataset/counter 재확인 → 같은 directory 내 `os.replace` 순서다. 실패하면 이전 카운터를
유지하고 자신이 만든 임시파일만 정리한다. 다른 writer의 lock은 제거하지 않는다.
stale lock은 자동 해제하지 않고 사람이 저장상태를 대조해야 한다.
replace 성공은 논리적 commit이다. 이후 lock 정리가 실패하면 성공된count를 실패로 보고하지
않고 경고를 남긴다. sudden power loss에 대한 directory journal 내구성 보장은 별도이며,
동일 lock을 무시하는 외부 writer와의 모든 race를 막는 DB transaction은 아니다.
실제 recorder 연결 시 dataset single owner를 확보해야 한다.

## execute preflight와 해제되지 않는 이유

사용자 첫 동작 승인 외에 다음 fresh read-only evidence가 필요하다.

- 카메라 fixed identity 설정 및 stage1 실제 검사 통과
- leader/follower canonical port가 서로 다르고 다른 LeLab/record 소유자 없음
- 6축 시작온도 모두50°C 미만(미측정, NaN,50°C는 거부)
- evidence age0~2초 (과거/미래값 거부)
- 확장 실행은 A:red 실물 파이프라인 검증

provider는 테스트 가능한 내부 인터페이스다. 기본 provider는 장치에 접근하지 않고
모든 미확인 항목을 불충족으로 반환한다. 사용자 지정 JSON이나 `--first-motion-approved`
하나를 전체 안전 통과 증거로 쓰지 않는다.

공식 v0.6.0 소스에서 다음을 확인했다.

- `robots/so_follower/config_so_follower.py`: disable_torque_on_disconnect=True,
  max_relative_target=None 기본값.
- `robots/so_follower/so_follower.py`: disconnect에 위 torque 값을 그대로 전달.
- `scripts/lerobot_record.py`: finally에서 dataset.finalize, robot.disconnect 호출.
- 따라서 raw CLI가 기존 RobotController의2°cap,55°C stop,Overload/HOLD,
  Ctrl+C/web HOLD,disconnect 토크유지를 보장한다고 볼 수 없다.
  **기존 controller에 연결된 recorder adapter가 구현·검증되기 전 unconditional BLOCKED**.

또한 `configs/dataset.py::stamp_repo_id`는 첫 생성 때 이름에 시각을 붙이고 record.py가
create 경로에서 이를 호출한다. `--resume`에서는 stamp하지 않는다. 별도 root를 지정해도
repo_id 변경이 존재하지만 그 자체가 조합별 dataset 분리를 뜻하지는 않는다.
구현된 counter에는 요청base `repo_id`와 최초생성의 `actual_repo_id`를 따로 보존한다.
첫 정상저장 때 검증된 recorder 내부 identity provider가 전달한 실제id를 받고,
이후에는 **동일root + 저장한actual_repo_id + --resume=true**를 사용하도록 mock검증했다.
재개 중actual_id, 요청base, root가 바뀌면 거부한다. 시각을 추측해id를 만들지 않는다.
공식 `DatasetInfo`에는repo_id필드가 없어 기존 metadata만으로actual_id를 복원할 수 없다.
그래서 실물adapter가 아직 없는 지금은 실제id수집이 NOT_RUN이다. 안전adapter차단과 별개로
timestamp자체를 금지하지 않는다. 미확인CLI옵션을 만들지 않았으며 초안은 실행용 명령이 아니다.

## 공식 CLI 옵션 검증

공식 tag v0.6.0 commit `30da8e687a6dfc617fcd94afc367ac7071c376ce`의 별도 snapshot에
child PYTHONPATH를 지정하고 Python3.12/이미 설치된 deps로 record --help 실행(exit0).
설치 distribution0.6.1은 변경하지 않았다. 실제 확인한 옵션만 초안에 포함한다:

`--robot.type`, `--robot.port`, `--robot.cameras`, `--teleop.type`, `--teleop.port`,
`--dataset.repo_id`, `--dataset.root`, `--dataset.single_task`, `--dataset.num_episodes`,
`--dataset.push_to_hub`, `--dataset.fps`, 최상위 **`--resume`**.

num_episodes=1, push_to_hub=false. resume는 read-only snapshot/counter가 일치하는
기존root에만 초안에서true. 공식 resume API의 explicit root 요구·DatasetWriter append 및
episode당 task 전달을 소스로 확인했으나 실제 create/append/장치 녹화는 NOT_RUN.
공식 문서/소스: [record](https://github.com/huggingface/lerobot/blob/v0.6.0/src/lerobot/scripts/lerobot_record.py),
[dataset config](https://github.com/huggingface/lerobot/blob/v0.6.0/src/lerobot/configs/dataset.py).

## Jetson 복사용 한 줄 명령

이 브랜치의 파일을 배치한 **저장소 루트**에서, 장치 없이 계획만 확인한다:

```bash
python3 -B scripts/vla_record.py --dry-run --combo A:red
```

9조합 계획만 확인하려면:

```bash
python3 -B scripts/vla_record.py --dry-run --expand-nine --combo C:blue
```

실제 승인 상태와 무관하게 현재 `--execute`는 exit2/BLOCKED다. raw command 초안을 복사해
직접 실행하여 wrapper를 우회하지 않는다. 별도 단계의 adapter 검증이 먼저다.

## 검증과 다음 단계

`python3 -B -m unittest discover -s tests -p 'test_vla_record.py' -v`:
42 tests PASS(exit0), 실패0/skip0. 실제 장치 실행은 제외(NOT_RUN).
상세 회귀/커밋/배포 상태는 [세션 기록](sessions/2026-10-28-vla-stage2-record-planner.md).

다음 시작 요청은 **“3단계 진행”**이다. 이번 작업에서3단계를 시작하지 않는다.
실제 녹화·장치 연결은 별도 현장 승인과 검증된 recorder safety adapter가 필요하다.
롤백은2단계 commit만 revert한다.1단계와 기존서비스·데이터에는 영향이 없다.
