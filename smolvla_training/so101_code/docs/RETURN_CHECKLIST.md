# 현장 복귀 체크리스트

모든 명령은 이 저장소 root 기준. **아래 현장 절차는 이번 세션에서 실행하지 않았다.**
이번 실행은 shell문법/LOCAL DRY RUN/mock만이다. 사용자 외출 중에는 장치에 접근하지 않는다.

1. 복구본 확인. 새 증분bundle은 base `5f03c571e223f86f422b6eb68af94a6ff05c78db`가 필요하므로
   빈 저장소에 독립 clone할 수 없다. base가 있는 기존 checkout에서 별도 branch로 가져온다.
   한줄: `git bundle verify /path/to/latest.bundle`
   기대: okay와 필수base 일치. 실패 시 missing commit/verify 출력을 가져온다. 원격fetch/push는 이번 미실행.

2. **복귀 첫 명령**: `bash scripts/jetson_preflight.sh --dry-run`
   기대: LOCAL_DRY_RUN, device_open=0, gpu_driver_queries=0. 이번 로컬에서 이 단계만 수행했다.
   오류면 Python환경·schema 오류 내용을 가져온다. Jetson 현장 read-only 파일조회는
   `bash scripts/jetson_preflight.sh --probe` (NOT_RUN), 생성 JSON만 가져온다.

3. 물리 카메라 식별. 현재 device=null이므로 먼저 장치 라벨과 by-id를 대조해 configs/vla_cameras.yaml에 기입한다.
   한줄: `python3 -B scripts/check_vla_cameras.py`
   기대: schema PASS는 영상연결 성공을 뜻하지 않는다. 점유 정리/현장 승인 후에만 `--capture`로 확인한다(NOT_RUN).
   오류면 장치별 by-id·화면 시점·새 점검 JSON을 가져온다. 번호 /dev/videoN만으로 판단하지 않는다.

4. 현장 전원차단 입회에서 leader/follower canonical USB와 단일 소유권을 확인한다.
   한줄(읽기 전용): `bash scripts/jetson_preflight.sh --probe`
   기대: 서로 다른 장치 신원, 점유 PID 확인. TX/RX 오류는 port/동시점유/배선전원/보정순으로 분리한다.
   자동재시도/torque-off를 하지 않는다. 오류 시 첫 오류와 점유 JSON만 가져온다.

5. 로컬 모델·환경 준비 후 벤치마크. 설치/다운로드 승인은 별도다.
   한줄(지금은 계획만): `python3 -B scripts/benchmark_smolvla_jetson.py --policy-path lerobot/smolvla_base`
   기대: NOT_RUN/null. 이후 로컬 checkpoint와 pinned source를 확인해 `--measure --policy-path /path/to/local/checkpoint --source /path/to/lerobot-0.6.0 --out /path/to/new-bench.json`을 실행한다(NOT_RUN).
   실패 시 extras/CUDA/OOM/cache 분류와 JSON을 가져온다. power/clocks 자동조회 미구현이며 실측을 추측하지 않는다.

6. A:red 첫 촬영은 **10회 파이프라인 검증용**, 최종 학습 충분량이 아니다.
   한줄: `python3 -B scripts/vla_record.py --dry-run --combo A:red`
   기대: 계획만 출력. 실제 recorder adapter는 미연결이므로 이 명령이 녹화를 시작하지 않는다.
   연결·단위·보정·저장완료/세션 기록을 먼저 검증해야 한다.
   공식cached smolvla.mdx:32,42는 약50회 출발점을 제안하고 그 예제에서25회가 부족했다고 보고한다.
   이것은 모든 작업의 고정 최소량이 아니며, A:red 실제 품질/분리평가에 따라 추가 촬영을 결정한다.

7. shadow 전 확인. 한줄: `python3 -B scripts/vla_run.py --mode shadow`
   현재 기대: BLOCKED. 실제 관측 provider·검증된 단위/감사분포·checkpointSHA·카메라 대응을 갖춘 뒤에만 실shadow 가능.
   이번에는 mock --shadow-sim만 시험했다. BLOCKED를 지우거나 JSON true로 우회하지 않는다.

8. approved first_run. 한줄(현재 readiness만): `python3 -B scripts/vla_run.py --mode live --execute --first-run`
   현재 기대: BLOCKED. RobotController streaming/HOLD 계약 부재를 먼저 해결해야 한다.
   해결 후에도 현장 전원차단·시작 Enter·arm0.5°·짧은step·stopHOLD·disconnect토크유지 확인이 필요하다.
   기존2°상한/55°C/Overload/HOLD는 완화하지 않는다. 원본보정과 한꺼번에 변경하지 않는다.

9. 실제 결과는 6단계 CSV에 사람이 기록한다. [6단계 사용법](VLA_STAGE6.md)을 따른다.
   먼저 `python3 -B scripts/vla_eval_report.py --csv data/vla_eval.csv validate`로 빈 템플릿과 SHA를 확인한다.
   실제 기록은 별도 로컬 원장에만 추가한다. mock 수치나 bench속도를 성공률로 사용하지 않는다.
   실패 시 새 run manifest/step logs/현장 관찰을 가져온다. 로봇 동작 영상은 자동 저장하지 않는다.
