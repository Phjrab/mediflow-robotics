# VLA 5단계: 무장치 정책 연결 계약·벤치마크·복귀 준비

2026-10-28. 시작 HEAD `596ee5fabb53fb0f818769fc84170f54835d6123`, clean `vla/main` 확인.
이번 작업: 소스 열람·새 코드·합성 mock·shell 문법/LOCAL DRY RUN만 수행.
**Jetson 접속0, 카메라/serial/모터0, GPU 실측0, 정책 모델 load/추론0,
shadow/live0, 가중치 다운로드0, 학습0, push0.** 7단계 웹은 시작하지 않는다.

## 먼저 발견한 연결 한계

`medicine_sorter/robot.py:110–148`의 RobotController는 TaskPlan의 stage를
adapter.execute_stage로 실행한다. `submit_bounded_target`, 연속관절 목표, last-target HOLD API는 없다.
따라서 새로운 MockStreamingController는 **제안된 모의 계약**이지 실제 RobotController의 구현이 아니다.
live 경로는 임의 JSON/승인 flag가 있어도 BLOCKED이며 기존 robot.py를 수정하지 않았다.

근거:

- robot.py:55–60 RobotAdapter의 stage-level 계약, :118 run, :141 read_status.
- :161–164 상태용 관절 순서 shoulder_pan/lift, elbow_flex, wrist_flex/roll, gripper.
- :229–231 상태용 bus의 arm DEGREES/gripper RANGE_0_100 선언은 :346 normalize=False
  raw register 읽기의 설정일 뿐, VLA streaming 명령 단위를 증명하지 않는다.
- 공식0.6 `robots/so_follower/config_so_follower.py:42` use_degrees 기본 true;
  so_follower.py:50은 설정에 따라 DEGREES 또는 RANGE_M100_100이다. gripper는 RANGE_0_100.
- 원래 fixed_demo.py:46–47 온도55/시작50미만, :222–224 arm2°·gripper2pp 상한,
  :78 Overload, :1391 Ctrl+C HOLD, :1402 establish_hold, :1593 disconnect(disable_torque=False).
  기존 경로에 검증 로직이 있어도 그것을 새 streaming API로 재사용할 계약은 아직 없다.
- `config.py`의 geometry degree나 과거 캘리브레이션 snapshot으로 실제 녹화 단위를 추정하지 않는다.
  실제 데이터/관측/정책/제어 좌표·단위 일치: **미확인, live 차단**.

## 공통 입력과 공식 API

유일한 mapping 정의는 `configs/vla_train.yaml`이다. get_rename_map을 학습과 추론 준비에서 공유한다.
old(dataset observation key) → new(policy key) 방향이며 action/task는 변환하지 않는다.
공식 `processor/rename_processor.py:36,46–47`에서 이 방향을 확인했다.
camera1/2/3가 특정 물리 시점을 의미한다는 공식 순서는 확인되지 않았다.
현재 mapping은 프로젝트 선택이며 실제 wrist/top/oblique 대응은 UNVERIFIED다.

공식 source0.6.0 commit `30da8e687a6dfc617fcd94afc367ac7071c376ce`와 설치 distribution0.6.1을 구분한다.
새 `configs/vla_inference_source_evidence.json`에 파일 SHA와 위치를 보존했다.

- SmolVLAConfig:29–30 chunk_size/n_action_steps 기본50/50, :113 소비길이는 chunk 이하여야 한다.
- modeling_smolvla.py:251 reset, :313 predict_action_chunk, :325 select_action.
  :348–350은 queue가 비었을 때 예측하고 n_action_steps만 넣어 하나씩 소비한다.
  소비길이 변경은 config 변경+reset이 필요하고 학습 chunk나 성능을 자동 개선하지 않는다.
- processor_smolvla.py:39 새 pre/post 구성, policies/factory.py:273 checkpoint processor 로딩.
- pretrained.py:162의 from_pretrained는 local_files_only를 지원한다.
- lerobot_rollout.py:24/32/33은 strategy base와 inference sync/rtc를 명시한다.
  이를 확인하기 위해 rollout/help를 실행하거나 장비를 probe하지 않았다.

SmolVLAWrapper는 **lazy import**하며 local checkpoint만 허용한다. source SHA/버전 혼용 검사,
offline 로딩, checkpoint pre/post, observation 사전rename 후 processor의 재rename 없음,
predict_action_chunk와 postprocessor를 연결했다. 정책 인스턴스 생성·실출력 검증은 NOT_RUN이다.
현재 CLI의 mock/shadow/live readiness는 이 wrapper를 호출하지 않는다.

## 정책 출력과 HOLD: 모두 모의 검증

`medicine_sorter/vla_policy_adapter.py`는 serial을 import하거나 직접 쓰지 않는다.
미래 경로는 policy → 검증 → 검증된 RobotController streaming adapter → motor여야 한다.
현재는 마지막 두 단계가 연결되지 않았으며 MockStreamingController만 존재한다.

검사 순서:

1. task는 vla_tasks.yaml 등록 문장. 빈문장 거부, --allow-unseen-task는 경고/manifest만 남기고 다른 gate는 면제하지 않는다.
2. 정확히 wrist/top/oblique 3개, camera YAML 해상도/RGB uint8, packet.task 일치.
3. state/frame timestamp 유한·미래 아님·max_age 이내, 카메라 간 skew 제한.
4. state/action floating shape6·유한성, 관절순서, 명시 단위/좌표/보정 범위.
5. 명시 fixture 근거가 있을 때만 radian↔degree. gripper percent 계약 미확인 시 거부하며 degree cap을 그대로 쓰지 않는다.
6. action과 현재/마지막 전송 목표 차이가 hard limit 초과면 첫 step도 HOLD.
   hard 이하 cap 초과는 arm 최대2°/gripper 최대2pp clip+count. sliding window 비율 초과는 정책-제어 불일치 HOLD.
7. 시작상태 audit binding/단위/분포 gate. 현재 구현은 synthetic evidence 주입 시험이다.
   기존 DATASET_AUDIT의 initial_state_variance만으로 시작 중심·허용구간을 확정할 수 없다.
   checked_at monotonic 값은 mock 과정 내 시간이지 실제 감사 파일 UTC 시각과 호환되는 증거가 아니다.
8. 시작50°C 미만, 실행55°C 이상·Overload·stop·Ctrl+C·예외는 HOLD, disconnect(disable_torque=False).

루프 fps는 주어진 meta/info.fps이며 고정 deadline을 사용해 jitter/step delay를 기록한다.
실제 meta.info 연결은 아직 없고 CLI mock은 SYNTHETIC fps30이다. max_steps/time을 적용한다.
추론 대기 한도는 timeout, n_action_steps/fps 소비시간, 남은 실행시간 중 최솟값이다.
지연/시간초과 시 마지막 **실제 모의 전송 확인 목표**를 HOLD하며 외삽하지 않는다.
추론은 controller 참조 없는 daemon thread에서만 수행한다. Python thread를 강제 종료할 수 없으므로
늦은 계산은 끝날 수 있지만 결과 queue는 버리고 추가 목표 전송/다음 추론을 하지 않는다.
이는 실제 GPU 작업 취소나 하드 실시간 보장이 아니다. production process isolation은 별도 검증 과제다.

--first-run은 mock에서 arm cap0.5°, 최대2초를 사용한다. 실제 first-run은 시작 Enter와
전원차단 입회·정지 HOLD 확인이 필요하지만 이번에는 요청/실행하지 않았다.

## 로그와 실행 모드

- `scripts/vla_run.py --mode mock --out NEW`: 전 provider가 mock, 합성 step JSONL+manifest+result만 생성.
- `--shadow-sim`: 같은 mock 관측/정책으로 target 전송도0. 실제 shadow라고 표시하지 않는다.
- `--mode shadow` / `--mode live --execute`: 인터페이스/현장 증거 부족으로 읽기 전용 BLOCKED.
- 증거 파일·오래된 camera check JSON·임의 approval은 live 인증으로 인정하지 않는다.

로그: run_id/step/timing/clip/raw_action/sent_action/state. 실패 시 HOLD event와 null 처리된 비유한값.
manifest: repoSHA·configSHA·공통mappingSHA·task·단위·증거 상태·checkpointSHA.
mock checkpoint는 null/MOCK_NO_MODEL. 영상저장0, 자동성공판정null, 기존out은 거부한다.

## 속도·메모리 벤치마크

`scripts/benchmark_smolvla_jetson.py` 기본은 계획만 출력한다.
--policy-path 기본 lerobot/smolvla_base는 식별자일 뿐 다운로드하지 않는다. --checkpoint는 alias다.
미래 --measure는 로컬 checkpoint + pinned source만 받으며 camera YAML 크기의 dummy3images,
state, 등록 task로 fp32/bf16/fp16 autocast 조건을 비교한다. 모델 전체 가중치를 임의 half로 바꾸지 않는다.
warmup은 latency 통계에서 제외한다. p50/p95/max, 소비시간과 비율, GPU peak/free, RSS/RAM available를 기록한다.
effective Hz는 n/(median_latency+n/fps) 순차 블로킹 추정값이지 모터 실측 주파수가 아니다.
latency<소비시간은 동기 후보일 뿐 끊김없는 제어 보장이 아니다. 이상이면 RTC/async가 필요하다는
설계 판정만 남기며 RTC 자체를 구현/실행한 것으로 표시하지 않는다.
속도/메모리 측정은 task 수행 성공률의 근거가 아니다.

이번 실제 측정값은 전부 null/NOT_RUN. --mock의 고정 수치는 SYNTHETIC_NOT_JETSON으로 표시한다.
power/clocks 자동조회는 미구현, UNKNOWN이다. 버전 확인 없는 nvpmodel/jetson_clocks 설정 명령은 제공/실행하지 않는다.
load 실패는 extras, CUDA/dtype, OOM, local weights/network, 기타로 분류하며 설치/자동다운로드로 고치지 않는다.

설치 격리 절차는 현장 버전 확인 후에만: 새 venv를 만들고 공식 source pyproject의 smolvla/training
extras 및 JetPack 호환 PyTorch wheel을 대조한다. 이번에는 venv 생성/pip 실행하지 않았다.
무조건 일반 CUDA wheel을 Jetson에 설치하지 않는다. 예시 준비 명령(이번 NOT_RUN):
`python3 -m venv /path/to/new-vla-env`.

## 복귀 점검과 검증 기록

`jetson_preflight.sh`는 Python 파일읽기/metadata 점검 wrapper다. 기본 --dry-run은 장치/GPU/system probe0.
--probe도 /etc·/proc·package metadata·by-id symlink와 /proc/fd 점유만 읽으며 device node를 열지 않는다.
출력은 새 reports/jetson_preflight_UTCtimestamp.json. 점유 snapshot은 독점권 증명이 아니다.
CUDA는 설치파일 build 문자열이며 CUDA 사용가능/VRAM 실측이라고 부르지 않는다.
이번에는 bash -n과 LOCAL DRY RUN만 실행했다. [현장 복귀 체크리스트](RETURN_CHECKLIST.md)를 따른다.

시작 전체회귀731 testsRun, failure6/error23/skip15. 직전730 실행과 실패·오류·skip 동일(새 metadata 시험1 추가).
skip15 실제분류: websocket-client1, 비공개 기록 fixture10, sandbox loopback4.
과거 기록의 '의존성15' 요약을 정정한다. 상세 안전 관련 test-name 분류는
`reports/vla-stage5-preflight-20261028/baseline_and_skip_classification.json`이며 static 이름분류이지
모든 안전 분기 통과 증명은 아니다. 실패 원인과 기존 safety 코드는 수정하지 않았다.
최종 counts/commit/bundle은 세션 기록과 검증 receipt를 참조한다. stage5 rollback은 해당 commit revert만,
원본 데이터·서비스 복구 작업은 필요 없다. NOT_PUSHED.
