# 11월 녹화 기준 SO-101 첫 동작 시험 준비

최신 상태: SSH로 직접 코드 저장·검사·모터 준비 진단을 수행했고, torque_so101_verified_goal_v3.py로 TORQUE_ON_HOLD를 확인했다. 정책 추론·live는 이번 후속 작업에서 실행하지 않았다. 기존 초기 준비 기록과 최신 실제 실행 결과를 구분한다. 운영자가 이번 채팅에서 확인한 11/02~11/03 Jetson LeLab/id=so-101/use_degrees=True/보정파일 미변경 사실을 근거로 채택했다. 저널 검색 결과가 없다는 사실을 보정 증거로 삼지 않았다. G5/G6 및 실기 성공 기록이 아니다.

## 0. 파일 준비 (장치 접근 없음)

현재 현장에는 준비 파일이 이미 있다. 초기 PASTE_TOOLS.sh/PASTE_HOME_ALIGN_UPDATE.sh는 보존용이며 토크 도구 재설치에 쓰지 않는다. 아래 초기 설치 설명보다 최신 torque_so101_verified_goal_v3.py와 TORQUE_PREP_FIXED.md를 우선한다. 초기 준비 당시에는 PASTE_TOOLS.sh의 전체 내용을 Jetson 터미널에 붙여 넣는 방식을 사용했다. 내부는 `cat > 파일 <<'EOF'`로 도구만 생성한다. 기존 파일이 하나라도 있으면 중단하며 덮어쓰지 않는다. 기존 v4/LeLab/데이터/체크포인트는 수정하지 않는다. v4는 이미 ~/so101_first_action_test_safe_20261104_v4.py에 있어야 한다.

생성 도구: collect_home_so101.py, torque_so101.py, check_live_evidence.py, nov_preflight.sh, nov_policy_run.sh, nov_env.sh.

후속 home 웹 검토본: 기존 도구가 이미 있는 사용자는 PASTE_TOOLS.sh를 반복하지 않는다. 별도 PASTE_HOME_ALIGN_UPDATE.sh의 전체 내용을 붙여 넣어 새 이름 home_align_web_reviewed.py, torque_so101_reviewed.py, nov_preflight_reviewed.sh, run_home_align_reviewed.sh를 만든다. 기존 파일을 덮어쓰지 않는다. 아래④-1과⑤는 이 검토본 이름을 사용한다. 최초 준비 사용자도 이 후속 도구를 함께 준비한다.

## 1. 전체137회 home 통계 및 JSON

Jetson LeLab Python으로 실행한다. pyarrow가 없으면 중단하며 설치를 자동으로 하지 않는다.

```bash
~/venvs/lelab-jetson-py310/bin/python -B ~/collect_home_so101.py --out ~/home_state_so101.json
```

원본 데이터 기본 위치: ~/.cache/huggingface/lerobot/Supermassive111/<repo이름>. 다른 저장 위치이면 실제 원본 여섯 폴더의 부모를 --datasets-base로 지정한다. 내려받기/변환/영상읽기/원본쓰기를 하지 않는다. parquet의 episode_index/frame_index/observation.state만 읽는다.

원본150회에서 빨강2/초록5/파랑6, 총13회를 제외한다. 빨강48/초록45/파랑44 및 정확히137회, 원본별첫프레임 중복/누락 없음, state/action6축 이름 일치, 30fps가 필수다. 과거 빨강·초록 데이터 정합성 보정은 에피소드 마지막 행만 조정한 것이므로 이 첫 프레임 계산에는 적용되지 않는다.

통계는 각 에피소드 첫 state를 동일 가중치로 계산하며 표준편차는 모집단 표준편차(pstdev)다. 전체 프레임의 정규화 통계를 대신 쓰지 않는다. 사용자 제공 기존 dry-run 자세와 평균 차이도 출력한다. 분산은 그리퍼0~100 단위와5축 degree를 구분해서 읽는다.

- 통과: 전체137회/색별48,45,44, HOME_JSON_CREATED 출력. 파일은 joint_names/state[6]/tolerance_deg=5.0.
- 중단: 누락 원본/부분 결과,137회 불일치, 기존 home 파일 존재, NaN/차원/순서 오류. 부분 결과로 home JSON을 만들지 않는다.
- 허용폭 제안: 최초5도. 최대시연시작편차가5도 이하면 유지. 초과하면 5도를 자동 확대하지 않고 여러 시작 자세 군집/카메라·팔 배치를 검토한다. 실제 통계를 아직 계산하지 않았으므로 더 큰 허용폭 숫자는 제안하지 않는다.
- 기존 dry-run 자세가 평균±5도 밖이라면 다음 단계 하드웨어 실행을 중단한다. 평균은 통계 기준이지 충돌 없는 경로가 아니다. 토크OFF 상태에서 숙련 운영자가 팔을 받쳐 수동으로 기준 근처에 놓고 확인해야 하며 자동 home 이동은 제공하지 않는다.
- home_gate는 5개 회전축에5도를 적용한다. 그리퍼는 각도 허용폭 대상이 아니며0~100 검사 및 별도 상태 확인이 필요하다.

부분 Windows 방법(원본4개, 예상사용63회): 노트북에서 collect_home_so101.py를 아래와 같이 실행한다. pyarrow가 있는 Python을 사용한다. 없으면 의존성 미설치로 중단한다. 현재 파일은3.10 문법으로 작성했다.

```powershell
python -B 'C:/Users/User/Documents/ChatGPT/자율설계/reports/nov_live_prep_20261104/collect_home_so101.py' --partial-windows --workspace 'C:/Users/User/Documents/ChatGPT/자율설계'
```

이는 원본 빨강10/초록10/파랑41/파랑9에서 동일 제외를 적용한 부분 결과다. 빨강 추가40/초록 추가40이 빠지므로 live home 근거로 사용하지 않는다. --out은 거부한다.

## 2. units JSON 생성

PASTE_UNITS.sh의 전체 내용을 Jetson 터미널에 붙여 넣는다. 사용자 확인 근거5번/7번을 포함한 ~/units_so101.json만 생성하며 기존파일이 있으면 중단한다.

필수 필드: joint_names, units, evidence, current_calibration_sha256, training_calibration_sha256, model_sha256. units는 [degree,degree,degree,degree,degree,percent_0_100]. 학습 보정 연결은 운영자 진술이며 자동 수집된 과거로그와 구별한다. 실제 현재 모델/보정 해시는 다음 단계와 v4가 재확인한다.

## 3. 모델 및 보정 SHA256 재확인

```bash
~/venvs/smolvla-py310/bin/python -B ~/check_live_evidence.py
```

장치/모델 import 없이 표준라이브러리로 파일을 읽는다. v4 모듈의 최상위도 표준라이브러리만 import한다.

- 통과: EVIDENCE_FILES_PASS, 현재 보정 SHA256=2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e.
- 모델 기대해시: b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654.
- 중단: 해시/정규화 통계/매핑/6축형식 불일치, home/units 파일 없음. 파일이 맞더라도 현장자세·충돌안전이 검증됐다는 의미는 아니다.

## 4. LeLab inactive 및 점유 확인

```bash
bash ~/nov_preflight_reviewed.sh
```

- 통과: PREFLIGHT_PASS. LeLab inactive, Astra브리지 active, 포트/카메라 경로 존재, sudo fuser에서 follower/손목/RealSense 비점유.
- 중단: active LeLab/다른프로세스 점유/권한오류/서비스 또는 장치 없음. 프로세스 자동종료나 서비스설정 변경은 하지 않는다.
- Astra 브리지의 /dev/video8 점유는 정상이다. 브리지 명령/픽셀형식/촬영배치를 11월 녹화와 다르게 바꾸지 않는다.

## 4-1. 토크 모두OFF 확인 → 웹으로 수동 home 맞춤 → Ctrl+C 종료

이번 현장 선택은 팔/물체를 지지한 뒤 follower 전원을 껐다 켜서 수동 자세 맞춤을 준비하는 것이다. 전원 재투입 자체가 토크OFF를 보증하지 않으므로 화면의 여섯 Torque_Enable=0을 실제로 확인한다. LeLab이나 다른 제어 서비스가 자동 시작돼 있으면 중단한다. 이 절차는 소프트웨어로 전원을 켜거나 팔을 이동시키지 않는다.

전원을 재투입하지 않고 일부ON 토크를 해제하는 대안도 있다. 팔/물체를 지지하고 모니터 및 다른 serial 사용 프로세스를 종료한 뒤 다음을 운영자가 직접 실행한다.

```bash
source ~/nov_env.sh
bash ~/nov_preflight_reviewed.sh && ~/venvs/smolvla-py310/bin/python -B ~/torque_so101_verified_goal_v3.py --disable --confirm-supported --execute
```

SUPPORTED 입력 후 TORQUE_OFF_SUPPORTED와6개 torque_enable=0을 확인한다. --disable은 일부ON/전체ON도 허용하며, 전체OFF 조건은 --enable에만 적용된다. --disable은 Goal_Position을 쓰지 않는다. 진단용 home이 없거나 잘못됐으면 차이를UNVERIFIED로 표시하지만 지지 확인 후 토크 해제에는 home을 요구하지 않는다. 통신/보정 검사가 실패하면 중단하며 자동 우회하지 않는다.

웹 모니터 시작(직렬 읽기만 하며 카메라/정책/토크/목표를 사용하지 않음):

```bash
bash ~/run_home_align_reviewed.sh
```

같은LAN의노트북브라우저에서 `http://<JETSON>:8765`를 연다. 모터 읽기는 한 스레드만 담당하고 HTTP는 캐시만 읽는다. 초록±3/노랑3초과~5/빨강5초과이며5축은도,그리퍼는퍼센트포인트다. 값 증감 방향과 팔의 물리 움직임 방향을 같은 것으로 단정하지 않는다. 원본의불안정한tty번호 대신 확인된follower by-id를 사용한다.

- 수동 맞춤 진행 조건: 여섯토크모두OFF, 온도50도미만, 최신관측, 오류없음. 하나라도ON이면 손으로 무리하게 돌리지 않는다.
- 맞춤 완료 표시: body5축home±5도, 그리퍼도표시기준±5퍼센트포인트. 가능하면초록±3범위로맞춘다. 그리퍼조건은v4의5축home각도게이트와구별된보조표시다.
- 중단: 관측갱신1.5초초과/연결끊김/읽기오류/토크ON/온도상한/비정상숫자. 이때이전초록표시는무효다. 독립안전장치나실행승인화면은아니다.
- 맞춘 뒤 터미널에서 Ctrl+C를 누르고 HOME_MONITOR_CLOSED를 확인한다. 모니터는포트만닫고토크를변경하지않는다. 그다음다시 nov_preflight_reviewed.sh를실행해follower비점유를확인한후⑤로간다. 창만닫거나브라우저탭을닫아서는serial모니터프로세스가종료되지않는다.
- 모니터가동중이면⑤의preflight가포트점유로거부하는것이정상이다. 모니터와torque/live도구를동시에실행하지않는다. 정상종료문구가없거나Ctrl+C후에도PID가남으면다음단계로넘어가지않는다.

## 5. home 근처에서 기존 목표=현재 위치를 읽기 대조한 뒤 토크 활성화

이 단계는 모터에 쓰며 물리 작용을 일으킨다. 팔/물체를 안정된 지지대로 받치고 손을 회전부에서 뺀 상태에서 운영자가 직접 실행한다. 이전 단계와 home 통계 확인을 마치지 않았다면 실행하지 않는다. 다른 앱을 새로 켜지 않는다.

```bash
source ~/nov_env.sh
bash ~/nov_preflight_reviewed.sh && ~/venvs/smolvla-py310/bin/python -B ~/torque_so101_verified_goal_v3.py --enable --execute
```

ENABLE 입력이 필요하다. 카메라/정책을 사용하지 않는다. 동일 SO101Follower의 버스만 사용하며 connect()의 설정/자동보정/토크순환은 우회한다. 포트를 연 직후 TIOCEXCL을 설정하고 현재 보정파일과 모터보정 일치를 확인한다. 이미열린 타프로세스는 이 ioctl로 배제하지 못하므로 직전 fuser 검사가 필요하다.

조건: 6축 토크OFF, 모든모터 위치모드0, 시작온도<50도, 부하 절댓값<200(원시 단위), Status=0, 실제자세가 home±5도. 원시 Present_Position과 기존 Goal_Position을 관절별 READ로 두 번 대조한다. 여섯 값이 정확히 같고 준비 도중 원시 위치가 바뀌지 않았을 때만 Torque_Enable=1을 한 번 보낸다. Goal_Position 쓰기와 SYNC_READ는 송신 가드에서 차단한다. 기존 목표와 현재 위치가 다르면 자동 수정하지 않고 쓰기 없이 거부한다. configure/운전모드/PID/Lock/보정 쓰기는 하지 않는다.

- 통과: TORQUE_ON_HOLD, torque6개 모두1, 온도정상. 종료는 disable_torque=False로 토크 유지.
- 중단: 어느 게이트든 실패, 움직임/튐/이상음/열/비정상전류. 읽기대조가 물리적 무동작을 보장하지 않으며 실패 후 일부토크가 켜졌을 가능성도 있다. 예외 시 자동토크OFF하지 않으므로 지지를 유지하고 상태를 확인한다. 위험이 발생하면 아래 비상 중단 절차.
- 현장 진단에서 Goal_Position 쓰기 직후 명시적 ON 없이 토크가 재활성화되는 구간을 재현했다. 따라서 기존 목표 복사 방식은 사용하지 않는다. 내부 보드/펌웨어 원인은 미확인이다. 기존 목표가 현재 위치와 다른 상황까지 자동 처리하는 일반 시작 절차는 제공하지 않는다.
- 거부 시 MOTOR_DIAGNOSTICS에 여섯 관절의 Torque_Enable/현재위치/home/home차이/단위/모드/온도를 출력한다. 예외에는ON인관절이름도포함한다. 최신 실제 실행에서는 6개 모두ON, 위치 모드0, 최고35°C를 확인했다. 이후 상태 변화까지 보증하는 상시 감시는 아니다.

## 6. 근거 파일을 포함한 v4 dry-run 1회

```bash
bash ~/nov_policy_run.sh dry
```

wrapper는 보정/모델근거 재검사와 점유검사를 한 뒤 v4를 --dry-run --robot-id so-101 --home-state ~/home_state_so101.json --unit-evidence ~/units_so101.json로 실행한다. task는 blue 문장,30Hz,step_cap0.5,20도/초 상한,10초다. 저장된 rename을 그대로 사용한다.

- 통과: chunks=1, executed_steps=0, 55도미만, DRY_REVIEW_NUMERIC_PASS. CSV의 첫목표차이가 모든관절1단위 이내인지 추가검사한다(그리퍼는퍼센트포인트). 시작이미지3장을 사람이 보고 위치/내용/촬영구성이 맞고 깨짐/정지가 없는지 확인한다.
- 중단: 경고/예외/프레임정지후보, 숫자조건불일치, 이미지3장누락/잘못된물리카메라/비정상색변경. 이전 통과 기록으로 현재실행을 대체하지 않는다. home 범위 검사는 dry-run에서 자동거부되지 않으므로 실제 live 전/토크활성화 시 새로 검사한다.
- v4의 상태출력은 근거가 있어도 UNVERIFIED라는 고정문구를 출력한다. 그 문구를 근거검사 실패와 혼동하지 않는다. live일 때 실제 verify_units 검사는 수행된다. 이번에는 v4를 수정하지 않았다.

## 7. 첫 live 최대5초

6번의 숫자/이미지검토와 현장안전을 운영자가 확인한 경우에만 다음 명령을 직접 실행한다. 넘어가는 자동연결은 없다.

```bash
bash ~/nov_policy_run.sh live
```

실제 내부 명령은 ~/venvs/smolvla-py310/bin/python -B ~/so101_first_action_test_safe_20261104_v4.py --robot-id so-101 --checkpoint ~/smolvla_model/pretrained_model --home-state ~/home_state_so101.json --unit-evidence ~/units_so101.json --task 'Put medicine bottle A into the blue basket' --fps 30 --step-cap 0.5 --max-deg-per-second 20 --first-action-limit 10 --temperature-every 5 --max-seconds 5 --live 이며 out은 새 시각경로다. v4가 LIVE 입력을 요구한다. 30Hz의50step은명목1.67초이고추론보고3.08초와별도다. 실제5초 상한은 추론시간도 포함하므로 추론이 늦으면0step 또는부분chunk일 수 있다. 시간을 늘리거나 cap을 키워 보완하지 않는다.

중단/보류조건: home이탈, 첫목표10도이상차이, NaN/차원/그리퍼범위, 온도55도이상, 부하/Status오류, 프레임정지, 충돌/예상밖방향. 정책성능/실기성공은 이 코드검사로 보증하지 않는다.

## 중단과 토크 해제

Ctrl+C는 Python이 처리할 수 있을 때 새정책전송을 중단하고 현재측정위치 HOLD를 같은버스로 한 번 시도한 뒤 토크를 끄지 않고 닫는다. 통신단절이면 HOLD실패가 가능하고 동기GPU추론/드라이버호출중에는 Ctrl+C나온도감시가 지연될 수 있다. 독립된정지버튼감시/RobotController통합은 없으며 G5/G6가 아니다.

예상밖움직임/접촉/끼임위험/정지불응이 있으면 소프트웨어종료를 기다리지 말고 준비한모터전원차단수단을 사용한다. 팔이처질수있으므로 시험전낙하영역을비우고 지지대를준비한다. 움직이는팔아래/관절틈으로손을넣어받치려하지않는다.

정상종료뒤에도 토크ON이며 온도감시는 종료된다. 방치하지 말고 팔과잡고있는물체를 안전한지지대에받친 뒤 정책프로세스종료/포트비점유를 확인하고 다음을 직접 실행한다.

```bash
source ~/nov_env.sh
bash ~/nov_preflight_reviewed.sh && ~/venvs/smolvla-py310/bin/python -B ~/torque_so101_verified_goal_v3.py --disable --confirm-supported --execute
```

SUPPORTED 입력이 필요하다. TORQUE_OFF_SUPPORTED와6개 torque=0을 확인한다. 일부실패면 지지유지. 전원이이미꺼졌거나 통신불가이면 반복통신으로복구를시도하지말고 현장상태를 확인한다.

## 남은 미확인

실제 home 파일은 운영자가 준비한 것을 읽어 검증했다. 이 후속 작업에서 137회 원본 통계 자체는 다시 계산하지 않았다. 현재 시작·종료의 읽힌 관절값은 같았지만 미세한 물리 움직임이나 현장 충돌 안전을 영상으로 검증하지 않았다. 실제토크활성화 무튐/현장충돌·관절물리한계/Load200의현장타당성/모터보호설정/독립정지수단/실시간성/실기성공률은 미확인. 세카메라의현재위치와렌즈·조명·Astra브리지동일성은 운영자가 확인한다. Windows부분자료는 전체home을대체하지 않는다. 초기 NOT_RUN 기록 뒤 SSH 저장/검사·serial 읽기·토크OFF/ON 및 현재 위치 목표 진단은 실제 수행했다. 새 정책 추론/카메라 열기/학습/live/push는 NOT_RUN. 최신 토크 준비는 성공했지만 G5/G6 통과는 아니다.
