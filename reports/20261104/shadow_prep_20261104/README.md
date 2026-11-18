# 3색 관측 파일 기반 shadow — 2026-11-04

## 범위와 현재 차단 상태
사용자가 확인한 Jetson 환경: ~/venvs/smolvla-py310, Python3.10.12, torch2.8.0 sm_87, LeRobot0.6.0, transformers5.5.4. 이 환경을 수정하거나 재설치하지 않는다.
이 도구는 관측 파일을 읽는 일회성 shadow다. serial/카메라/RobotController를 생성하지 않고 토크·목표 위치 전송·HOLD 명령을 호출하는 경로도 없다. 기존 robot.py는 수정하지 않았다. 카메라 장치를 직접 열어 촬영하는 기능은 없다.

현재 학습 시 use_degrees/캘리브레이션 근거와 현재 LeLab의 정규화 관측 내보내기 경로는 미확인이다. 따라서 템플릿은 confirmed=false/값null로 두었고 실제 추론은 차단된다. 이 템플릿을 근거 없이 true로 바꾸지 않는다. 범위로 단위를 추정하지 않는다. 현재 환경에서 모델 로딩 통과는 사용자 보고이며, 이 도구로 재실행한 결과는 아니다.

## 단위 대조 결과
training_ranges.csv/json은 전달받은 최종137회 체크포인트 통계를 표준 라이브러리로 읽은 실제값이다. 입력 observation.state 및 출력 action 각각6축의 min/max/mean을 포함한다. 통계는 카메라/로봇 접근 없이 읽었다.
학습 관절 단위: 미확인. 공식 SO follower 기본 use_degrees=true만으로 실제 녹화 설정을 확정할 수 없다.
현재 확인한 기존 robot.py: SO101ReadOnlyStatusAdapter가 MotorNormMode.DEGREES(그리퍼 RANGE_0_100)를 지정하더라도 read_status()에서 normalize=False를 전달한다. 결과 present_raw는 엔코더 raw이며 degree가 아니다. 참고 파일: 전달 코드 so101_code/medicine_sorter/robot.py 229행(모드 지정), 338~346행(읽기와 normalize=False).
따라서 present_raw를 observation.state로 직접 쓰는 경로는 부적합, 중단이다. 현장 LeLab의 실제 관측 정규화/캘리브레이션은 아직 확인하지 못했으므로 전체 단위 일치는 미확인이다. 자동으로 raw를 degree로 변환하지 않는다.

## serial 단일 소유 방식
LeLab이 현재 포트를 소유한 상태를 유지한다. 기존 소유 프로세스가 이미 읽고 정규화한 상태/프레임을 읽기 전용 관측 묶음으로 내보내고 이 도구는 파일만 읽는다. 이 도구는 serial 포트를 새로 열지 않으므로 같은 포트의 동시 오픈이 없다. LeLab 서비스를 자동 중단/재시작하지 않는다.
기존 포트 소유자 내부에서 파일을 내보내는 기능/API는 미확인이다. 이 전달물에는 LeLab 서버 패치나 검증되지 않은 HTTP endpoint가 없다. 그 내보내기 경로를 확인·연결하는 단계가 아직 필요하다. 관측 파일 없이 이 도구만으로 실시간 하드웨어 관측이 생기지 않는다.
생산자는 원시 영상/값에 가공 오버레이를 추가하지 않고 RGB640x480 프레임3장과 상태를 기록한다. 영상 파일은 프레임별 새 이름으로 작성해 보존하고, 마지막에 JSON을 임시 파일→rename으로 게시한다. reader는 JSON의 각 파일 SHA256과 바이트를 비교한다. 원본 학습 데이터셋에 쓰지 않는다. 캡처가 중단됐는데 로컬 시각만 갱신하면 안 된다. 실제 획득 시각을 기록한다.

## 입력 형식
unit_contract.template.json: 학습/현재6축 순서, 5개 회전축degree+그리퍼percent_0_100, 캘리브레이션/생산자 설정 SHA256, 근거 파일·줄·SHA256. 근거 원문 파일이 함께 있어야 한다. 확인된 JSON 사본을 별도 새 파일로 작성한다. 프로그램은 구조·해시·일치를 검사하지만 근거 문장의 의미는 사람이 확인해야 한다. 학습 데이터와 실제 녹화 설정의 대응 근거가 필요하다. 다른 단위의 2도 환산은 미구현이므로 그 경우도 차단된다.
observation_packet.template.json: 정규화된 state6, joint_names, units, state_mode=normalized, 실제 상태/카메라 획득 Unix 시각, 근거 설정/캘리브레이션 해시, 3개 원본 데이터셋 키 아래 프레임 파일명·SHA256·RGB·640x480. source_file 경로는 packet.json 기준 상대 경로다.
누락·raw·미확인·불일치·NaN/Inf·관측 오래됨이면 즉시 예외로 중단한다. 상태/영상 최대 나이2초(설정 상한5초), 서로의 획득 시각 차이0.1초(상한0.5초)다. 모델 로딩 후 다시 최신 packet을 읽으므로 생산자는 로딩 중에도 새 관측을 게시해야 한다. 관측을 조작해서 신선도 검사를 통과시키지 않는다.

## 모델과 카메라 경로
end_effector → camera1 / ceiling_vertical → camera2 / ceiling_oblique → camera3.
입력은 원래 observation.images.end_effector, observation.images.ceiling_vertical, observation.images.ceiling_oblique 및 observation.state로만 생성한다. RGB640x480을 CHW float32[0,1]로 구성하고 저장된 전처리로 이름 변환/정규화를 한 번 적용한다. 모델 내부 영상 전처리를 사용하고 별도 임의 crop이나 사전256리사이즈를 추가하지 않는다. preprocessor를 우회하거나 state를 임의로 다시 정규화하지 않는다.
저장된 policy_preprocessor.json / policy_postprocessor.json 및 각 통계 파일을 make_pre_post_processors로 로딩한다. 예측 API는 확인한 predict_action_chunk이며 결과[1,50,6]을 요구한다. 저장된 단일행동 후처리를 각 step에 적용해 역정규화한다. 추가 rename_map을 만들지 않는다.
모든 task에 동일한 관측과 동일한 난수seed101을 사용하고 policy.reset을 호출한다. 이것은 동일 관측에서 문장별 출력을 살펴보는 검사이며 색 구분 성능/성공률 검증이 아니다. 세 task를 읽는 동안 매번 새로운 관측을 붙이는 실시간 루프가 아니다.
HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE을 사용하므로 모델/토크나이저가 없으면 다운로드하지 않고 오류로 중단한다.

## 사용자가 Jetson에서 실행하는 명령
자료를 새 폴더 ~/smolvla_shadow_tools에 복사했다고 가정한다. 스크립트/입력 템플릿/실제 근거를 각각 구분한다.

첫 단계: 모델 파일과 통계만 검사(장치/모델 추론 없음).
```bash
bash ~/smolvla_shadow_tools/run_shadow_snapshot.sh
```
PLAN_ONLY와 통계, 단위 미확인이 나온다.

다음 단계: 단위 근거 확인 및 단일 소유자의 실제 관측 내보내기 연결이 완료된 경우만 실행한다. 다음 경로는 사용자가 준비할 실제 입력 형식이며, 지금은 입력파일/연결 자체가 NOT_RUN이다.
```bash
bash ~/smolvla_shadow_tools/run_shadow_snapshot.sh --run --contract ~/smolvla_shadow_inputs/unit_contract.json --packet ~/smolvla_shadow_inputs/packet.json --out ~/smolvla_shadow_results/first_three_tasks
```
기존 출력 폴더가 있으면 거부한다. 재실행 때는 새 결과 폴더를 사용한다. 모델은 기본 ~/smolvla_model/pretrained_model. 실행 래퍼에서 검증된cuSPARSELt0.6.3.2 경로를 지정한다. Python3.10에서 모델이 로딩됐다는 사용자 보고를 따라 이 환경을 사용하며 별도 설치를 하지 않는다.

## 출력
- end_effector.png / ceiling_vertical.png / ceiling_oblique.png: 추론에 사용한 시작 관측 RGB640x480 각1장. 입력 프레임 파일은 읽기만 한다. 장치를 새로 열어 촬영하지 않는다.
- actions.csv:3문장×50행동×6축=900행. 색/task/chunk_step/관절/단위/현재값/행동/차이/절대차이/추론시간/첫행동경고를 기록한다.
- summary.json: 관절별 최대·평균 절대차이, 회전축 전체 최대·평균 차이, 그리퍼 별도 차이, 추론시간, GPU할당/예약 메모리 최고값, 입력근거해시. 모든 기준은 같은 현재 상태이며 연속행동끼리의 차이는 아니다.
- 시간은 CUDA synchronize로 구간을 맞춘 전처리+전체chunk+후처리 시간이다. 모델 로딩/PNG저장은 제외하며 첫 호출 워밍업은 포함된다. 벤치마크/실시간제어성능으로 해석하지 않는다.
첫action의 5개회전축에서 절대차이>=2degree이면 경고 관절명을 출력한다. 그리퍼에는 2degree 기준을 적용하지 않는다. 추론 행동은 clip하지 않고 원본값을 기록한다. 어떤 값도 모터에 전송하지 않는다.
오류 시 traceback 전문을 출력하고 중단하며 후속 task를 실행하지 않는다. 실패 전에 생성된 PNG/부분 CSV는 보존한다. summary.json의 SHADOW_SNAPSHOT_COMPLETED가 있어야3색 전체완료다.

## live와 구분
live는 이번 구현에 없다. 55도정지/시작50도미만/1회2도cap/Overload·정지버튼HOLD/종료토크유지는 기존제어계층에서 별도로 적용해야 하며 이 도구의 경고가 안전게이트를 대체하지 않는다. 로봇에 HOLD조차 보내지 않는 분석도구다.

## 검증 결과
명령: python -B -m unittest discover -s reports/shadow_prep_20261104 -p test_shadow_snapshot.py
6테스트 통과, 실패0, 제외0, exit0. 9개의 잘못된 관측 사례는 한 테스트 안의 subTest로 검사했다. 단위미확인/캘리브레이션불일치/raw/NaN/차원/카메라순서·RGB·크기·신선도/50step·경고의 논리만 확인했다. 장치모듈이나 모델 import 없이 검증했다.
bash -n run_shadow_snapshot.sh exit0. 실제 최종 체크포인트를 이용한 PLAN_ONLY 통계조회 exit0. 학습 원본·체크포인트·기존코드 변경 없음. push/Jetson접속/모델추론/PNG·행동CSV 실제생성/포트소유자관측내보내기 연결은 NOT_RUN.
