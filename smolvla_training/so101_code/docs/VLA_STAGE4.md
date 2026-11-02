# VLA 4단계 — 무학습 설정·명령 검토·분할/통계 보호 준비

2026-10-28. 시작 HEAD `37667d53ba64eb2a833dc559aed8a79d328f9809` 확인.
**실제 학습·smoke·정책 모델 로드/추론·다운로드·GPU 계산·장치 open·로봇0**.
학습 실행 코드 연결은 구현했다. 실제 학습 배포/모델 forward·backward는 검증하지 않았다.
초기 commit `1c540b3`의 항상 차단되던 실행기는 후속 보완으로 교체했다.
단계5는 시작하지 않는다. 원격 push도 하지 않는다(NOT_PUSHED).

## 변경 전 확인과 안전 계층 보존

Jetson `jetson3@127.0.0.1`에 BatchMode=yes, StrictHostKeyChecking=yes,
ConnectTimeout=5, ConnectionAttempts=1로 hostname 읽기1회만 요청했다.
현재 실행기의 socket 권한 거부(exit255)로 접속 상태 확인 불가. 재시도·임의주소·암호 입력 없음.
torch2.11.0+cu130의 조회는 cuda_available=false/device_count=0, NVML 초기화 경고였다.
현재 GPU/VRAM을 확인하지 못했으며 GPU 고장이라고 확정하지 않는다. 모델/텐서 GPU 계산은 없다.

동일 Python3.12.13와 `tests/run_vla_nohardware.py`를 사용했다. 기존 checkout을 reset하지 않고
각 기준을 새 temp git archive로 읽었다. 전체 discovery의 결과:

| 기준 | testsRun | failures | errors | skipped |
|---|---:|---:|---:|---:|
| 기준5f03c57 | 587 | 6 | 23 | 15 |
| 2단계d5136c0 | 656 | 6 | 23 | 15 |
| 3단계37667d5 | 691 | 6 | 23 | 15 |
| 4단계 최종 작업본 | 722 | 6 | 23 | 15 |
| 4단계 실행기 보완 최종 | 730 | 6 | 23 | 15 |

실패/오류/skip ID와 snapshot 경로를 정규화한 사유 전체가 동일했다.
원인 분류는 현장URDF/JSON fixture 누락·기존 ArUco 기준 불일치29건, 의존성부족 skip15건,
장치serial 필요0·새 안전계층 회귀0·기타0이다. 일부는 setUpClass 오류이므로 testsRun에서
개수를 빼서 PASS 수를 계산하지 않는다. fake-live 테스트는 fake transport이며 실장비 호출이 아니다.
`robot.py`, RobotController, safety, 기존 pose/calibration/제어 코드는 수정하지 않았다.

상세 test 이름·사유는 `reports/vla-stage4-preflight-20261028/regression_comparison.json`,
정규화 비교는 `reason_comparison.json`에 있다. 전체 통과로 표시하지 않는다.

이전 **174 PASS는 전체 discovery가 아니라 다음 subset**이다:

```bash
python3 -B -m unittest discover -s tests -p 'test_vla_*.py'
python3 -B -m unittest discover -s tests -p 'test_fixed_demo.py'
python3 -B -m unittest discover -s tests -p 'test_demo_web.py'
python3 -B -m unittest discover -s tests -p 'test_sorter_*.py'
python3 -B -m unittest discover -s tests -p 'test_camera_preview_joint_ws.py'
```

당시 각각69/64/7/31/3개였다. 현재 test_vla_*는 새 테스트가 추가돼 개수가 다르다.

## 파일과 기본 설정

- `configs/vla_train.yaml`: 새 파생dataset root, stage3 split·sidecar·감사 경로,
  seed101/batch4/steps100000/save_freq5000/workers2/cuda, W&B/Hub off.
  batch4는 메모리 부담을 낮추기 위한 **후보**이지 RTX3060에서 검증된 적합값이 아니다.
- 모델 후보는 `lerobot/smolvla_base`. chunk_size/n_action_steps 등 base 옵션은 덮어쓰지 않는다.
  소스 class 기본은50/50이지만 실제 Hub checkpoint config는 다운로드하지 않아 미확인이다.
- `configs/vla_verified_options.json`: 공식0.6.0 help와 소스 파일 SHA 증거.
- `scripts/vla_train.py`: 기본 읽기 전용 계획·gate·manifest 초안.
- `scripts/vla_train_launch.py`: 명시적 execute 전용 실제 공식 main 연결. 이번에는 FakeUpstream만 호출했다.
- `vla_train_data.py`: train-only numeric 통계 및 주입식 dataset/training 연결.
- `vla_train_evidence.py`: 명시적으로 요청할 때만 full CPU 감사와 원본전체 해시 receipt를 새 외부폴더에 생성.
- `vla_compare_act_cmd.py`: ACT/SmolVLA 비교 초안. 실행하지 않는 참고용이다.
- `vla_eval_offline.py`: 가짜 action chunk의 관절별/시점별 MAE만 계산.

기본 dry-run은 subprocess/network/GPU 호출·파일 생성0이다. 버전은 importlib.metadata로 읽는다.
기존 checkpoint·데이터·통계를 수정하지 않는다. 전체 파일 체크섬 검사는 CPU/디스크를 읽을 수 있다.

## 하나의 카메라 대응 정의

유일한 대응표는 train YAML의 rename_map이며 `get_rename_map(config, ...)`로 읽는다.
학습 준비와 향후 추론은 이 함수를 공유해야 하며 다른 파일에 대응표를 복사하지 않는다.
현재 wrist→camera1, top→camera2, oblique→camera3는 **프로젝트의 새 매핑 선택**이다.
pretrained camera 번호가 특정 물리 시점을 절대 의미한다고 가정하지 않는다.
방향/누락/중복, dataset 입력키, 로컬 모델 config의 input_features 존재를 검사한다.

모델 입력 키의 추가 근거는 `configs/vla_model_metadata_evidence.json`이다.
별도 노트북의 주채팅에서 공식 Hub revision `5e8d12a6e2975b0e5e5fce7c8caf47c371d257b6`
config.json을 읽어 state+camera1/2/3, action shape6, chunk_size50을 확인했다고 보고했다.
원본2299bytes/SHA256 `650584b56c104720f7a3c91d1ec6bec9e8de8ac11e60c92ba2fa82d93eda147d`는
**외부 관측의 보고값**이며 이 실행 환경에서 독립 검증한 값이 아니다.
여기서는 curl DNS실패와 web reader 접근불가로 원본 metadata를 저장하지 못했다.
따라서 모델 입력 키 근거는 attributed observation으로 보완하되 runtime model_config=null,
물리 카메라 대응 UNVERIFIED, 실제 가중치/학습/추론 NOT_RUN을 유지한다.
원본 metadata의 push_to_hub=true와 별개로 학습 override는 false다. 이 근거 JSON은 모델 config가 아니다.

실제 물리 대응은 UNVERIFIED. stage1 devices도 null이다. VERIFIED_FROM_RECORDING으로 바꾸려면
별도 증거 JSON에 dataset_binding, cameras_config_sha256, rename_map,
evidence[{file,line,contains,sha256}]를 제공해야 한다. 이름이나 과거 /dev/video 번호만으로 확정하지 않는다.
config 파일의 문구 자체가 물리적 사실을 자동 증명하는 것은 아니며 현장 기록이 필요하다.

## 공식 버전·옵션과 분할

별도 공식0.6.0 source commit `30da8e687a6dfc617fcd94afc367ac7071c376ce`의
`lerobot_train --help` 실제 exit0,3974줄. 설치 distribution은0.6.1 그대로다.
help에서 dataset.episodes, dataset.eval_split, rename_map, resume, config_path 등을 확인했고,
policy.path는 configs/train.py:159의 별도 parser 경로도 확인했다. 파일별SHA는 verified_options에 있다.
dry-run에서 help나 버전 subprocess를 새로 호출하지 않는다.

공식 DatasetConfig의 episodes는 int list다. stage3 train 목록을 `--dataset.episodes=[...]`로
직접 전달할 수 있다. 그러나 factory.py:144~166의 eval_split은 task별 마지막 일부 episode를
떼는 방식이므로 세션 split과 다르다. 후보 명령은 eval_split=0으로 고정한다.
평가목록은 stage3 JSON의 명시적 목록을 adapter가 그대로 유지하며 자동비율 분할로 대체하지 않는다.

## 통계 누수 방지와 실행 차단

공식 DatasetMetadata는 dataset_metadata.py:184에서 global meta/stats.json을 읽는다.
episode 목록만 제한해도 그 통계가 자동으로 train-only가 되지 않는다.
`train_statistics`는 명시적 train row만 사용해 action/state min/max/mean/std/count를 계산한다.
SmolVLA의 VISUAL=IDENTITY, STATE/ACTION=MEAN_STD 계약이 로컬 model config와 맞을 때만 허용한다.
전체자료 image 통계나 eval row에서 numeric 통계를 가져오지 않는다.

`partitioned_datasets`는 공식 factory와 같은 호출형식을 주입받아 train/eval 목록을 각각
지정하고, 두 데이터셋의 numeric 통계에 동일한 **train-only** 값을 넣는다.
`invoke_training`은 주입된 training module의 make_train_eval_datasets hook을 임시 교체하고
train 호출 후 finally에서 원복한다. **이 연결은 FakeTrainingModule/FakeDataset으로만 검증했다.**
공개 launch entrypoint는 source SHA를 재확인하고 공식0.6 trainer/parser/factory만 import한다.
공식 `main()`에 verified argv를 전달하며, train wrapper가 remote/job/env/W&B/Hub 옵션을
상류 remote dispatch보다 먼저 거부한다. factory hook은 명시 train/eval+train-only 통계를 공급하고
finally에서 원복한다. 상류 파일을 수정하지 않는다. 기본 정책은 로컬 `config.json`과
nonempty `model.safetensors`가 필요하며 offline 환경에서만 로드한다. 이 파일 존재 검사는
weights 무결성/전체 backbone cache/모델 적합성 보장이 아니다. 누락 캐시는 다운로드하지 않고 실패한다.

CLI `--execute`는 preflight를 재검사하고 실제 부족 조건이 있으면 exit2/BLOCKED다. raw upstream 명령은
`CANDIDATE_ONLY_NOT_EXECUTABLE`이며 복사 실행하지 않는다. manifest는
verified_upstream_candidate_args와 실제 wrapper argv를 구분한다. 계획에서는 actual_launch_args=null이다.
raw CLI 실행은 통계 보호 adapter를 우회하므로 준비 완료나 실행 방법으로 제시하지 않는다.
영구 차단 상수는 제거했다. 이번 작업에서는 실제 실행 경로를 호출하지 않았다.
실행 시에는 신규 runroot/run_manifest.json을 먼저 쓰고, 상류 output은 아직 없는 runroot/train을
사용해 상류 existing-output 거부와 충돌하지 않는다. 실패 결과는 run_result.json에 유형만 남긴다.
원본·파생 데이터는 write/network/device audit guard로 보호하며, 캐시는 runroot/dataset-cache에 둔다.
guard는 실행 프로세스 수명 동안 유지된다. shell/subprocess로 학습을 우회 실행하지 않는다.

## 실행 전 검사 계약과 manifest

- 원본 아닌 파생본·원본 source binding·원본/파생 episode remapping·각fingerprint 일치.
- split을 실제 sidecar/provenance로 재생성해 동일성 확인: session 누수/unknown train/잘못된 인덱스 금지.
- full 영상 감사/공식 reload PASS, train에 audit FAIL 포함0. stale/타dataset report는
  audit SHA+metadata binding+전체 source tree hash receipt로 거부한다.
- base config/카메라 대응·물리 증거·단위 증거. 범위만 보고 degree/percent를 추측하지 않는다.
- `--accept-unit-unknown --unit-unknown-reason "..."`은 사유를 manifest에 남기고 단위 gate만
  면제한다. 세션·데이터·GPU·실행금지 gate를 면제하지 않는다. 빈사유/비밀값처럼 보이는 사유 거부.
- GPU/VRAM은 실제 execute의 CUDA 가용성·free memory 조회가 필요하다. 기본 여유 메모리 하한8GiB는
  입장 조건이지 batch/model fit 보장이 아니다. OOM 가능성은 남으며 실제 smoke는 NOT_RUN이다.
  dry-run은 GPU를 호출하지 않고 미확인으로 남긴다. CLI에 임의 GPU JSON override는 없다.
- output 기존 존재 거부, 원본 안/상위 output 금지. Hub/W&B off, 유료클라우드 자동실행 없음.

manifest 초안은 repoSHA, 설치버전과 계획source버전, meta/info·split·camera·정규화config·rename·
원본/파생 provenance·통계 SHA, argv와shlex 표시문자열, seed, prepared_at/start_time=null,
단위 예외사유를 담는다. 계획 child env는 source PYTHONPATH와 offline 설정만 담고
토큰·비밀번호·환경 전체를 기록하지 않는다. shell=False, 실제 학습은 별도 shell이 아닌 보호된 프로세스 내 main 호출이다.

## 재개

`--resume-config`와 `--previous-manifest`를 함께 받아 원본 checkpoint를 보존한다.
새 output이 필요하며 기존 output 덮어쓰기를 허용하지 않는다. dataset root/episodes, source/split/
rename/model/stats 계약, seed/steps/batch/workers/save_freq와 checkpoint train_config를 대조한다.
공식 후보는 `--resume=true --config_path=...`이며 source 구현은 CLI override를 지원한다.
resume에서는 `policy.path`를 넣지 않는다. 공식 resolve가 policy.path를 resume보다 우선하기 때문이다.
가짜 main 시험에서 old checkpoint 보존·새 runroot/train·resume/config_path argv를 확인했다.
실제 optimizer/weights 복원은 **NOT_RUN**이며 현재 자료/장치 증거 부족으로 CLI는 BLOCKED다.

## ACT 비교 근거

기존 `lerobot_training/train_act_a_v3_trim05_3cam_100k_3060.sh`를 실행하지 않고 읽었다.
batch8/steps100000/workers2가 명시돼 있다. cameras와 chunk_size는 shell에 명시돼 있지 않아
예전 런타임 기본값을 추측하지 않았다. sourceSHA·해당 줄은 reports/.../historical_act.json에 있다.
비교 초안에서는 동일 파생root/episodes/seed/steps/batch를 유지하되 ACT는 language 입력이 없어
**명시적 A:red-only 자료에서만** 생성한다. 여러조합·세션 미확인은 거부한다.
새 ACT에는 rename_map을 붙이지 않는다. 공식 config는 pretrained 없는 rename_map을 거부한다.
실제 비교 실행은 NOT_RUN이며 기존 모델/체크포인트는 그대로 둔다.

## 명령 (계획 확인만)

PC에서 저장소 루트:

```bash
/home/USER/miniconda3/envs/lerobot-3060/bin/python -B scripts/vla_train.py --dry-run
```

사용자가 별도로 마련한 클라우드에서도 source/자료 경로를 조정한 뒤 같은 계획 확인만 한다:

```bash
python3 -B scripts/vla_train.py --config configs/vla_train.yaml --dry-run
```

클라우드 인스턴스 생성·결제·모델 다운로드·실학습 명령은 수행하지 않았다.
실행 코드의 진입점은 같은 script의 `--execute`다. 모든 증거와 로컬 모델 캐시가 필요하다.
현재 설정에서 실행하지 않았으며, 이 문서는 이번 실제학습 실행 승인을 뜻하지 않는다.

## 테스트와 다음 단계

초기4단계31개 + 기존VLA104개 =135 PASS, skip0. 실행기 보완 시험은 별도 최종 보고서에 기록한다.
초기 전체722 testsRun의 failure6/error23/skip15는 변경 전691개 실행의 항목·사유와 완전히 동일했다.
초기 대조는 `reports/vla-stage4-preflight-20261028/final_regression_comparison.json`에 보존한다.
source metadata/가짜 모델config/가짜 GPU provider를 사용한 테스트이며 모델을 실행한 것이 아니다.
단위override·누락/역방향rename·split/원본 remap/stale감사·FAIL포함·기존output·resume 불일치,
dry-run 무호출, secret 미포함, eval값 변경에 train 통계 불변, 주입 train hook 및 종료복원 검증.
offline MAE는 episode 종료 이후 horizon을 mask하며 로봇 성공률이 아니라고 표시한다.

추가8개 시험: 실제 부족조건 차단, fake upstream.main의 argv/stats/manifest 전달, hook복원,
remote 차단, GPU 하한 거부, preflight 사이 자료변경 거부, resume 경로/old checkpoint 보존,
공식0.6 실제 dataset 객체의 train[0,2]/eval[1] 선택·train-only stats·source불변이다.
마지막 시험은 작은 synthetic parquet/MP4를 CPU로 읽었으며 trainer·policy·optimizer를 import하지 않았다.
최종 VLA143 PASS/skip0, 전체730 testsRun에서 기존 failure6/error23/skip15의 항목·사유는 변경 전과 동일.
최종 증거는 `reports/vla-stage4-preflight-20261028/launcher_final_comparison.json`이다.
현재 real dataset split·단위·카메라 증거, local base config/weights, GPU/VRAM과
실제 모델 forward/backward/normalizer/resume 학습은 미확인이다. 실행기는 구현, 실학습 검증은 미완료다.
**학습/smoke/모델평가/로봇구동은 모두 NOT_RUN.** 다음 요청 문구는 **“5단계 계획 검토”**.
그 문구만으로 실제학습·로봇 실행을 승인한 것으로 간주하지 않는다.

## 3단계 보완 안내와 복구

3단계 선별본은 공식0.6.0 data/metadata helper를 사용하고 영상은 재인코딩 대신 전체복사+원래PTS를
유지했다. synthetic은 실제0.6.0 source loader로 reload했다. 기존ACT6종은 row/container/공식샘플
감사와 압축SHA 확인만 했고 전체프레임 검사·선별본실생성은 NOT_RUN이다.
세션 필드: schema_version,episode_index,dataset_binding,counter_path,run_id,session_id,combo,task,
started_at,camera_config_sha256,episode_fingerprint. counter 확정과 맞지 않는 sidecar는 읽기 거부.
3단계 한줄(자리표시자 교체 필요):

```bash
python3 -B scripts/vla_audit_dataset.py --root /path/to/dataset --out /path/to/new-audit --official-source /path/to/lerobot-v0.6.0
```

3단계만 되돌릴 때는 의존하는4단계부터 별도branch에서 revert한 뒤 docs37667d5→구현4bb48da 순서다.
이번4단계 rollback도 commit revert이며 원본데이터/서비스복구는 필요 없다.
push NOT_PUSHED. 초기증분bundle `.local/backups/vla-stage4-20261028-incremental.bundle`은 보존한다.
보완본은 `.local/backups/vla-stage4-20261028-launcher-v2.bundle`로 별도 저장한다.
필수 기준commit은 `5f03c571e223f86f422b6eb68af94a6ff05c78db`; self-contained 아님.
이전bundle 유지, verify/SHA/최종commit은 옆 receipt JSON에 기록한다.
