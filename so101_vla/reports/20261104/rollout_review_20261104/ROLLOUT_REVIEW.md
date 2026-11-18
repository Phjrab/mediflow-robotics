# LeLab rollout 및 관절 단위 검토 — 2026-11-04

## 결론
정책 로딩/추론/모터 전송 경로는 존재하지만 현재 읽은 사본 그대로는 프로젝트의 2도 제한·55도 정지·HOLD 조건을 충족하지 않는다. 따라서 첫 live 명령은 제공하지 않는다. 읽기 전용 관측 파일 shadow를 재사용한다. 기존 코드/체크포인트/데이터 원본은 수정하지 않았다.
검토 범위: 저장소의 lerobot_training/jetson_py310_src/lelab/rollout.py 및 공식 lerobot0.6.0 휠. Jetson 설치본과 동일한지는 미확인이다. reviewed_source_hashes.json에 읽은 사본의 해시를 기록했다. 설치본은 read_installed_sources.py로 파일만 읽어 비교할 수 있다.

## 단위 근거
- 사용자 판단: 학습5축 degree, gripper0~100. LeLab record.py에서 use_degrees를 지정하지 않았다는 사용자 정보가 있다.
- 실제 읽은 원본 meta/info.json(파랑41회 및 빨강기존10회): robot_type=so_follower, observation.state/action=float32[6], 관절이름 순서 shoulder_pan/shoulder_lift/elbow_flex/wrist_flex/wrist_roll/gripper.pos. units/use_degrees/calibration 필드는 없다. meta만으로 degree를 확정할 수 없다. 최종 병합 데이터 info.json 원본은 이 PC에 없으며 체크포인트/준비기록만 전달받았다.
- 공식소스 config_so_follower.py42행: use_degrees=True 기본값. so_follower.py50~60행: 몸체는 이 값에 따라 DEGREES 또는 RANGE_M100_100, 그리퍼는 RANGE_0_100. 182~187행: get_observation이 bus.sync_read(Present_Position)를 호출. motors_bus.py1124~1129행: normalize=True 기본값.
- 공식소스 motors_bus.py850~873행: DEGREES는 (raw - (range_min+range_max)/2)*360/(resolution-1), RANGE_0_100은 보정범위로0~100 정규화한다.
- 전달된 configs/so101_follower_calibration_bounds_20261008.json 5축 기록은 normalization_mode=DEGREES, resolution_steps=4096 및 원본보정해시를 명시한다. 그러나 10월8일의 제한 범위 요약본이고 당시 실제 보정 원본이나11월녹화config와의 대응은 없다. 원본 motor calibration의 range_min/range_max 숫자만으로는 degree/정규화모드를 확정할 수 없다. 그리퍼 보정 원본도 이 요약에 없다.
판정: 사용자 설명대로 record가 옵션을 생략했고 해당 구현을 그대로 사용했다면 degree/0~100이라는 소프트웨어적 근거는 성립한다. 작업상 단위 가정은 이를 따르되 녹화시 실제 record.py·설치소스·보정파일과의 일치는 미확인이다. 값 범위로 추가 추정하지 않는다. 이전 robot.py의 present_raw(normalize=False)는 이 관측과 다른 값이며 그대로 사용하면 불일치다.

## LeLab rollout 제어 경로
저장소사본 lelab/rollout.py:
- InferenceRequest 44~51행: follower포트/config, policy_ref, task, cameras, duration. max_relative_target/rename_map/온도제한 요청필드는 없다.
- _resolve_policy_path 113~143행: 존재하는 로컬폴더를 그대로 policy.path로 사용. 로컬정책지정은 절대경로가 안전하다. Python Path는 literal~를 자동 확장하지 않으므로 UI문자열~/smolvla_model/...는 피한다. 셸에서는 "$HOME/smolvla_model/pretrained_model"로 확장할 수 있다.
- 298~320행: teleop/record/inference 활성플래그로 같은서버의 충돌을 거부. 외부프로세스 포트점유를 막는 OS배타잠금은 이 함수에서 확인하지 못했다.
- 336~362행: sys.executable -m lerobot.scripts.lerobot_rollout --strategy.type=base --policy.path=... --robot.type=so101_follower --robot.max_relative_target=5.0 --fps=15 --interpolation_multiplier=2. 현재 요청한2도/학습30fps와 다르다. request에서2로 바꾸는 필드가 없으므로 UI경로는 코드수정없이 변경 불가.
- 실제 실행 Python은 LeLab서버의 sys.executable. 별도로 로딩 성공한 smolvla-py310과 LeLab서버 환경이 같은지 미확인. LD_LIBRARY_PATH도 서버프로세스에서 상속한다. 터미널에서 설정했다고 이미실행중인 서버에 반영되지 않는다.
- 388~398행: tracking gate는 absolute ACT만 허용. 기존 ACT tracking gate 파일을 SmolVLA에 그대로 적용할 수 없다.
- 401~413행: 캘리브레이션 input에 newline을 보내며 기존보정을모터에쓰는 경로가 가능하다. rollout은 읽기전용 기능이 아니다.
- 445~471행: 중지는 terminate,5초후kill. 이것을 목표유지HOLD와 동일하다고 볼 수 없다.

공식 lerobot0.6.0 휠:
- rollout/context.py236~243행: 로봇생성/connect 및 초기관절읽기. so_follower.py92~108,115~124,159~173행: connect가보정/모터설정/토크맥락에진입할수있다. 정책출력을보내기전에도 하드웨어쓰기가가능하다.
- rollout/context.py308~325행: CLI rename_map이비어있으면 기대모델카메라명과 실제로봇이름을비교해거부가능.392~399행에서는 저장된전처리의 rename을 cfg.rename_map으로 override한다. 따라서 저장매핑에만의존하면안된다. robot.cameras키를end_effector/ceiling_vertical/ceiling_oblique로 두고 동일한CLI매핑을 명시하면 이론상연결가능. 대안으로 request.cameras를 camera1/camera2/camera3 이름으로 직접 구성하면 빈CLI매핑에서도모델입력명과일치시킬수있다.이경우저장매핑을빈값으로override한경로를사용하므로별도중복rename을하지않는다.물리대응은손목=camera1/수직=camera2/사선=camera3이어야한다.실제설치본동일여부/CLIhelp검증은NOT_RUN.
- rollout/inference/sync.py114~122행: 전처리 → policy.select_action → 후처리 → 행동관절순서재정렬.
- rollout/strategies/core.py290~304행: inference → 보간 → robot_action_processor → robot.send_action. 프로젝트RobotController를경유하지 않는다. 따라서 기존안전계층이자동적용되지않는다.
- so_follower.py223~231행: max_relative_target을 현재측정관절값 대비 clip후 Goal_Position전송. use_degrees=true일때 float2.0은5회전축2도 및그리퍼2퍼센트포인트에해당한다. 이전목표끼리2도제한과는다르다. 소스에해당CLI설정은있지만 현재설치본--help확인은NOT_RUN.
- so_follower/rollout 소스에서55도값/온도주기읽기/시작50도미만게이트를찾지못했다.그리퍼Overload_Torque설정은있으나55도정지/HOLD를보장하지않는다.장치펌웨어보호설정은미확인.
- strategies/base.py62~76행: 동기추론이지연되면주기를넘겨경고한다.실행취소나HOLD타임아웃으로바꾸지않는다. SmolVLA의50step큐는정책내부에서소비하며명목30fps이면50/30초다.현재LeLab설정15fps소비일경우50/15초로달라진다.모터보간30Hz가정책소비30fps를뜻하지않는다.
- LeLab _rollout_inference_args는 relative_action 또는groot일때RTC선택.일반SmolVLA가자동RTC가된다고단정할수없다.RTC큐/지연보상은있어도55도보호·항상HOLD와동일하지않다.
- scripts/lerobot_rollout.py230~239행: 루프중예외/Ctrl+C에서teardown.하지만context빌드는이try앞이라모든시작실패가동일정리경로라고보장못한다.
- rollout/configs.py248행 return_to_initial_position=True 기본. strategies/core.py119~157행: 종료시에3초간초기자세복귀목표전송후disconnect. so_follower/config_so_follower.py31행: disable_torque_on_disconnect=True 기본.즉기본종료는HOLD/토크유지가아니다.

## 현재 사용 판정과 첫 시험
현재 사본 그대로의 실기: 요구안전조건 불충족. 2도설정만붙인live명령을안전한명령으로제시하지않는다. --temperature_limit=55 같은확인하지못한옵션은만들지않는다.
향후live설계시 필요한것: 단일소유제어경로에서시작50도미만/운전55도정지/Overload·정지버튼·통신오류HOLD/NaN·큰점프·지연중단/종료목표유지및토크유지를검증.허용소스에서max_relative_target=2.0 및use_degrees=true를확인해사용.초기자세복귀와토크해제옵션도명시적으로검토해야하며이들옵션만으로온도정지가생기지는않는다.기존코드수정금지범위에서는추가보호미구현상태를그대로보고한다.

읽기전용대안: 이미만든 reports/shadow_prep_20261104/shadow_snapshot.py 재사용. 데이터셋카메라키와저장전처리/후처리,3문장×50step CSV/차이/시간,RGB프레임3장저장.모터/serial접근경로없음.정규화된관측은LeLab단일포트소유자가내보낸파일로만받는다.이내보내기연결은여전히미확인/NOT_RUN이므로실시간shadow가완료됐다고표현하지않는다.실제record/calibration근거확인없이unit_contract를자동으로true로바꾸지않았다.

관측파일없는첫단계(통계조회만,사용자가파일을Jetson에복사한후):
```bash
bash ~/smolvla_shadow_tools/run_shadow_snapshot.sh
```

## Jetson 설치본 소스 확인 — 사용자 실행
새 파일 read_installed_sources.py를Jetson홈에복사한뒤:
```bash
python3 -B ~/read_installed_sources.py
```
표준라이브러리로두환경의허용된소스파일만읽고해시/record옵션사용여부/정해진원본info.json/기본보정폴더만출력한다.모듈import,rollout/--help실행,장치/서비스접근,파일쓰기없음.보정경로가커스텀이면나오지않으므로미확인.출력을받아reviewed_source_hashes.json과비교하고원본record/calibration연계를확인할수있다.원격접속하지않았다.

## 수행·미실행
수행: 원본meta2개,보정요약,기존LeLabrollout,공식휠사본읽기/정적분석,보고서와읽기전용수집도구작성.수집도구Python문법검사통과.기존소스수정없음.
미확인: Jetson설치본동일성,녹화record원문옵션생략,실제보정동일성/그리퍼보정,LeLab서버와정책로딩환경일치,실제온도·지연,관측exportAPI.
NOT_RUN: Jetson접속/--help/rollout/shadow실행/추론/카메라·serial·로봇접근/push.실기첫시험명령없음.
