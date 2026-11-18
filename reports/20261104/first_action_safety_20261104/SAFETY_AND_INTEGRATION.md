# 첫 동작 시험 후속 보강

이 결과는 코드/모의 검사이며 G5/G6 및 실기 성공 근거가 아니다. LeLab 패키지와 RobotController 코드는 변경하지 않았다. Jetson 복사/실행/추론은 NOT_RUN.

## 구현

- 기본 --max-seconds=10. LIVE 입력 유지. 기본 dry-run은 관측/추론/기록을 하지만 모터 목표 및 토크 쓰기 0. 사용자 실행 시 카메라/serial은 연다.
- 토크ON/위치모드 조건은 live 전용이다. dry-run은 토크OFF여도 읽기/계산만 가능하며 자동 토크활성화나 동작모드 설정은 하지 않는다.
- LeRobot max_relative_target=2.0 유지. 추가 소프트웨어 스텝 제한 기본0.5도, 속도20도/초, 주기30Hz. 0.5×30=15도/초. cap×Hz>속도상한이면 실행 전에 거부. 그리퍼는 도가 아니라0~100이며 스텝당2포인트.
- 이전 전송 목표와 현재 측정값 양쪽 제한 구간의 교집합으로 목표를 제한한다. 교집합이 없으면 HOLD. 지연 후 밀린 명령을 연속 전송하지 않는다. 설정Hz와 실측Hz를 CSV에 남긴다. 물리적인 모터 속도 자체를 보증하는 기능은 아니며 실제 추종 속도는 NOT_RUN.
- 매 chunk 첫 action은5축 중 하나라도 현재값에서10도 이상이면 live 거부. 50개 모든 action과 state의6차원/NaN/Inf/그리퍼0~100 검사.
- 시작50도미만, 운전55도미만, Present_Load 절댓값 임계 기본200(원시 단위), Status 비영 값을 모두 거부. 읽기 실패도 예외로 종료한다. 200은 보수적으로 선택한 시험 설정이고 현장 타당성 미확인. 온도/부하/Status는 기본5프레임 간격과 chunk 전후에 확인하며 동기 추론 중에는 새로 읽지 못한다.
- Ctrl+C/예외/시간상한 뒤에 live 전송 시도가 있었다면 같은 bus의 현재 위치를 읽어 HOLD Goal_Position을 한 번 보낸다. 카메라 연결상태에 의존하지 않는다. 통신 단절이나 잘못된 상태값이면 HOLD 전송이 실패할 수 있고 그 사실을 출력한다. 토크 해제/재보정/자동home이동은 하지 않는다.
- home JSON과 학습/현재 보정 대응 근거가 없으면 live 차단. home은 통계 평균/0도/보정범위 중간값으로 추정하지 않는다. 사용자에게서 검증된 home 정보를 받아야 한다. 입력 확인 후 시작 자세/온도를 다시 읽는다.
- 연속 관측의 카메라 영상 바이트가 같으면 정지 프레임 후보로 HOLD. 정적인 장면도 잘못 거부할 수 있다. 센서 원본 타임스탬프가 없어 producer 신선도를 완전히 증명하지 못한다.

## 카메라와 모델

저장 policy_preprocessor.json의 rename을 정확히 한 번 적용한다. 원본 데이터 키를 config.json의 모델키와 문자열 그대로 비교하면 정상 rename도 오검출하므로, 저장 rename의 source 집합과 데이터키를 비교하고 destination 집합과 모델 input_features를 비교한다. 누락/중복/방향불일치는 거부한다.

|역할|입력 데이터 키|저장 전처리의 모델 키|이전 확인 장치|
|---|---|---|---|
|손목 USB|end_effector|camera1|/dev/v4l/by-id/usb-Generic_USB_Camera_200901010001-video-index0|
|천장 수직 Astra|ceiling_vertical|camera2|/dev/video8 (기존 브리지 필요)|
|천장 사선 RealSense|ceiling_oblique|camera3|/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435_236223023645-video-index0|

follower 포트: /dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00. 사용자 물리 확인 기록을 따름. 위 값은 과거 확인값이며 현재 연결 상태는 미확인. LeLab 서비스는 종료해야 한다.

보정 ID는 미확인이다. 보유 lelab/teleoperate.py 226~233행은 setup_calibration_files(...) 반환 follower_config_name을 id로 쓴다. serial 번호와 보정 ID를 같다고 추정하지 않는다. 현재 record.py는 보유 사본에 없다. --robot-id에 실제 녹화 때의 ID, 필요하면 --calibration-dir를 명시한다.

config의 smolvla/6축 action/학습 steps>0는 파인튜닝 후보 근거다. 이것만으로 실제 가중치가 base와 다르다는 증명이 되지는 않는다. base 또는 판단불가는 BASE_OR_UNVERIFIED로 표시하고 live 거부한다. 후보도 보정/단위/모델해시 근거가 없으면 live를 거부한다. train_config의 policy.path가 smolvla_base라는 이유만으로 base라고 판정하지 않는다(미세조정의 초기모델일 수 있음).

정규화 safetensors의6축 min/max/mean, 카메라 매핑, 관절 순서, 현재 보정 해시를 출력한다. 통계 범위는 단위 증거가 아니다. meta/info.json에는 단위가 없으며 학습 당시 보정과 현재 보정의 동일성은 미확인이다. 근거 JSON은 새로 만들어 내지 말고 원본 기록으로 확인해야 한다.

--unit-evidence JSON 형식(값은 실제 확인된 것만): joint_names=[shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper], units=[degree,degree,degree,degree,degree,percent_0_100], training_calibration_sha256, current_calibration_sha256, model_sha256, evidence(파일/줄 근거). 현재 구현은 두 보정해시가 같아야만 허용한다. 모델 파일도 실제 해시로 확인한다. 이 JSON은 사용자 근거 진술이며 자동으로 녹화 이력을 복원하지 않는다.

--home-state JSON 형식: joint_names(위 순서), state(6개 실측값), tolerance_deg(5축 허용폭). home 자세는 아직 미확인이고 예제 숫자는 제공하지 않는다.

## 확인한 LeRobot 소스

보유 공식 lerobot-0.6.0 휠 기준이며 Jetson 설치본과 동일한지 미확인.
- so_follower/config_so_follower.py31~42행: disable_torque_on_disconnect, max_relative_target, use_degrees.
- so_follower/so_follower.py92~108/159~173행: connect가 보정/설정/토크맥락에 진입. 이 실행기는 기존과 같이 bus.connect(handshake=False)를 쓴다.
- 같은 파일223~231행: 현재측정값 기준 clamp 후 Goal_Position. 235~238행: disconnect 옵션 전달.
- motors/motors_bus.py529~539/546~560행: handshake 없는 포트열기, disable_torque=False 포트닫기.
- motors/feetech/tables.py85~88행: Present_Load, Present_Temperature, Status.208~210행: Present_Load sign-magnitude 부호비트10. 부호해석은 라이브러리 read()가 수행.
- motors/feetech/feetech.py317~325행: read decode 경로. Overload_Torque는 tables.py71행의 설정레지스터이므로 읽어서 현재 overload 여부로 취급하지 않는다. Status 비트별 원인 해석은 미확인; 모든 비영 값을 거부한다.

## RobotController 통합에 필요한 변경 (설계만)

현재 스크립트는 RobotController를 거치지 않으며 독립적인 serial 소유자다. 보유 medicine_sorter/robot.py110~145행의 RobotController.run은 단계 기반 TaskPlan/execute_stage 인터페이스다.

1. 정책 어댑터 입력: RGB3장+취득시각, 정규화된6축 상태와 단위/관절순서, task. 출력: 역정규화된6축 목표와 chunk/step 식별자. 정책/언어/카메라 모듈에는 bus를 넘기지 않는다.
2. RobotController에 연속목표 요청, 현재관측 제공, HOLD, 종료상태 API를 설계하고 기존안전게이트를 통과시킨다. 현재 API에 임의로 execute_stage를 반복해서 연속제어라고 취급하지 않는다.
3. serial 포트/보정/토크/온도/부하/상태읽기의 소유자를 하나로 고정한다. LeLab/별도프로세스와 동시개방을 OS배타잠금과 점유확인으로 차단해야 한다. 현재 스크립트에 OS배타잠금은 없으므로 LeLab종료만으로 배타성이 증명되지는 않는다.
4. HOLD는 센서/카메라 실패와 독립적으로 같은 제어소유자가 처리하고, 통신오류 때의 한계를 명시한다. 정지 버튼/Overload/온도/부하/속도/각도한계와 로그를 Controller 안에 통합한다.
5. 신선도/첫목표/home/단위/보정/모델근거 검증을 제어게이트로 이동한다. 단일경로와 쓰기0 fault injection을 검증한 후 별도 실기절차로 진행한다. 이번에 robot.py를 수정하지 않았다.

## 복사와 이후 순서

이번 작업에서는 복사하지 않았다. 노트북에서 Python 스크립트만 새 이름 so101_first_action_test_safe_20261104.py로 옮긴다. 기존 Jetson 파일은 덮어쓰지 않도록 먼저 파일 존재 여부를 사용자 터미널에서 확인한다. 이후에는 --help/표준라이브러리 문법검사 → 보정 ID 확인 → 사용자 dry-run 순서다. home/단위근거 미확인 상태에서 live 안내를 실행하지 않는다.

롤백: first_action_safety_20261104/so101_first_action_test.before.py를 reports/so101_first_action_test.py로 복사하면 이번 Python 수정 전으로 복귀한다. 원본 LeLab/데이터/체크포인트 변경 없음.
