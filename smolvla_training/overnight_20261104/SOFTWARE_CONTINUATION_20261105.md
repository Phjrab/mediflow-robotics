# 11월 5일 소프트웨어 계속 진행 결과

**로컬 소프트웨어 수정·검증·배포 준비를 완료했다. Jetson 연결이 시간 초과여서 이번 변경은 아직 Jetson에 배포하지 않았다. 실기 준비 완료나 G5/G6 통과가 아니다.**

## [ACTUAL_EXECUTED]

위치: Windows 작업 폴더. 로봇·serial·카메라를 열지 않았고 실제 모델 추론·학습도 실행하지 않았다.

| 명령/작업 | 결과 |
|---|---|
| `python -B run_software_checks.py` | 관련 52개 통과: 신규26 / 기존 관련26, 실패0 / 오류0 / 제외0, exit0 |
| Python3.10 AST 문법 / Bash3개 문법 / 배포 PowerShell AST | 통과. 마지막 셸 cwd 수정 뒤 관련2개 및 Bash문법 재확인 통과 |
| 모델 ZIP에서 설정·정규화 통계 6개만 읽어 영수증 SHA 대조 | 6개 원본 바이트 일치. 가중치는 추출·수정하지 않음. 개인 경로는 분석용 JSON 사본에서 마스킹 |
| `python -B software_preflight.py --checkpoint <분석용 메타데이터>` | SOFTWARE_CONFIG_PASS, hardware_ready=false. 실제 모델 로딩 아님 |
| `python -B cached_action_replay.py ...` | 기존 실제 예측18개 재생. 18개 모두 HOLD, 가상 상태 적용0 / 레지스터 쓰기0 |
| 기존 RobotController 소스 스냅샷 + MockVLAStageAdapter 통합 시험 | 정상 단계 / NaN fault / 물리모드 불일치 시험 통과. 실제 SDK·POSIX 장치 호출 없음 |
| Jetson SCP 1회 및 이후 SSH 연결 재확인1회 | 각각 ConnectTimeout8초에서 timeout. 인증·명령 실행 전 막힘 |

전체 프로젝트의 legacy 회귀나 기존 장치 시험을 다시 돌린 것은 아니다. 테스트별 목록과 범위는 reports/SOFTWARE_TESTS.json, SOFTWARE_TEST_CLASSIFICATION.json, SOFTWARE_TESTS.txt에 있다.

## [FILES / 변경한 것]

### 1. 카메라 입력 계약

- `vla_observation_config.json`에 카메라3대 경로·640×480 RGB·30fps·최소3초 예열을 모았다. 바뀐 RealSense 경로를 사용한다.
- `observation_contract.py`가 키·크기·uint8·빈 task·경로 중복·serial 경로 오입력·짧은 예열을 거부한다.
- 모델 이름 변환의 원본은 체크포인트의 `policy_preprocessor.json` 하나다. 관측은 end_effector / ceiling_vertical / ceiling_oblique 키로 만들고 저장 전처리가 camera1/2/3으로 한 번만 바꾼다.
- 정책 입력은 raw RGB uint8을 Float32/255로 변환한 batch다. JPEG 미리보기를 모델 입력으로 넣지 않는다. 관절 값은 변환하지 않고 데이터의 degree5개 / gripper0~100 선언을 유지한다.
- `dry_inference.py`, `capture_cameras.py`, 미리보기 점검 도구, `nov_preflight.sh`가 같은 설정을 읽는다. dry 경로의 1초 예열·옛 RealSense 경로를 수정했다.
- `nov_readonly.check_stop()`은 dry의 첫 단계로 유지했다. 중단 기록을 삭제·무시하거나 일부ON을 허용하는 기능을 만들지 않았다. 이번 세션에서는 dry를 실행하지 않았다.

### 2. 장치 없는 사전점검과 재생

- `software_preflight.py`: 파일만 읽는다. 모델·GPU·장치를 열지 않으며 메타데이터 PASS와 hardware_ready를 분리한다.
- 로컬 분석 폴더에는 모델 가중치가 없다. 6개 설정/통계만 검증한 결과이므로 실제 모델 실행 PASS로 사용하지 않는다.
- `serial_stop_present`는 **해당 사본 폴더의 파일 존재**만 뜻한다. Windows에서 false라고 Jetson 중단이 해제된 것이 아니다. scope 필드를 추가했다.
- `cached_action_replay.py`: 원본을 읽기만 하며 기존 예측을 재사용한다. 실제 현재 state·온도·토크·현장 승인 값을 만들어 넣지 않는다. 외부/물리 backend는 받지 않고 InMemoryRobot만 허용한다.
- `MockVLAStageAdapter`는 기존 RobotController의 stage API와 연결하는 모의 어댑터다. robot.py를 고치지 않았다. 시험 스냅샷은 해시와 원본 커밋2a72321을 기록했고, 실제 장치 함수가 호출되면 시험이 실패하게 했다.

### 3. 안전 판정 보강

별도 v5 제안 모듈만 보강했다. 기존 v4 및 실제 로봇 제어 코드는 수정하지 않았다.

- 모델/단위/카메라/현장 확인은 문자열이나 숫자의 truthy 값이 아니라 정확한 True여야 한다.
- 관절 한계 배열과 근거가 없으면 HOLD. 테스트의 ±180도는 합성 fixture일 뿐 실제 관절 한계가 아니다.
- HOME 허용폭5도 초과, 첫 step뿐 아니라 이후10단위 초과 점프, 한계 밖 action/측정값/이전목표, 비정상 상태 형식은 HOLD.
- 0.5도 cap, 20도/초 설정 상한, 시작50°C 미만, 55°C 정지, 최대5초 제안 조건은 완화하지 않았다.
- Ctrl+C/예외/닫힌 backend/첫 단계 건너뛰기를 모의 경로에서 HOLD로 처리한다. HOLD는 목표 위치나 토크를 쓰지 않는다.
- 이 모듈은 물리 실행기가 아니다. 숫자 제안 결과에도 hardware_authorized=false가 붙는다.

## [RESULTS / 저장 예측 재생]

새 추론이 아니라 야간에 저장한 red/green/blue 각3회차의 시작·중간, 총18표본×50×6 예측을 읽었다.

| 구분 | 결과 | 해석 |
|---|---:|---|
| 소프트웨어 제어 HOLD | 18/18 | 현장 확인·현재 상태·한계 근거를 임의로 채우지 않아 전송 단계로 가지 않음 |
| 기록된 지연이50/30초를 초과 | 18/18 | 별도의 숫자 검사. 실제 controller HOLD 사유와 구분 |
| 0.5도/30Hz 제한의 clip 비율 정지 후보 | 예측18/18 | 완벽 추종 가정의 계산이며 실기 정지 관측이 아님 |
| 같은 제한을 녹화 action에 적용한 정지 후보 | 8/18 | 제한 프로파일과 시연 궤적의 관계도 봐야 함. 모델만의 실패라고 단정하지 않음 |
| 실제 명령·가상 상태 적용 | 0 / 0 | 실제 로봇 상태나 성공률을 추정하지 않음 |

0.5도×30Hz는 목표 변화량15도/초다. 이 계산을 실제 관절 속도 보장으로 사용하지 않는다. clip 임계값을 높이거나 지연 조건을 풀어서 통과시키지 않았다. 실제 상태가 없는 한 모터에 이 궤적을 보낼 수 없다.

## [오프라인 GPU 비교 준비]

`benchmark_offline_precision.py`를 만들었다. 기본 실행은 계획만 출력하며 --run-offline을 명시하면 저장 데이터로만 실행한다.

- 같은3색 시작 표본·seed101·저장 전처리/후처리로 baseline(no autocast), CUDA FP16 autocast, CUDA BF16 autocast를 비교한다.
- 모드별 첫 지연·warm5회 평균/최대·GPU peak·RSS·관절별 action 차이를 기록한다. 몸체 degree와 그리퍼 퍼센트포인트를 합쳐 평균내지 않는다.
- 파라미터 dtype을 전수 기록하므로 autocast 비활성을 순수FP32라고 단정하지 않는다. 체크포인트 use_amp=true도 기록한다.
- 원본 가중치 저장·변환·재학습은 없다. 성능이 좋아 보여도 실제 실행 정밀도를 자동으로 바꾸지 않는다.
- 실제 GPU 결과는 **NOT_RUN: Jetson 연결 불가**다. 새로운 속도나 정확도 수치를 지어내지 않았다.

API 근거는 [PyTorch2.8 autocast 소스](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/amp/autocast_mode.py)다. 실제 설치 환경에서의 실행 호환성·속도는 연결 복구 후 검증해야 한다.

## [RobotController 연결 범위]

모의 검증 경로는 `기존 RobotController.run → MockVLAStageAdapter.execute_stage → SoftwareOnlyController → v5 판정 → InMemoryRobot`이다. 실제 controller가 mock 단계의 stop/close를 호출하는 것까지 확인했다.

기존 물리 LiveRobotAdapter는 fail-closed 상태다. 물리 연속 목표/측정 상태/독립 정지·온도 감시/직렬 포트 단일 소유·검증된 관절 한계의 통합은 완료하지 않았다. 이 모의 성공을 프로젝트 전체의 단일 실기 제어 경로나 G5/G6 통과로 기록하지 않는다. 학습·추론 중복 rename과 단위 불일치도 현장값을 추측해서 해결하지 않는다.

## [BLOCKED / NOT_RUN]

- Jetson 연결 timeout 두 번. 전원·Wi-Fi·네트워크 중 원인은 미확인이다. 터널·네트워크 설정 변경·재부팅·서비스 조작을 시도하지 않았다.
- 이번 수정의 Jetson 배포, 새 실제 raw 카메라 SDK 확인, 실제 GPU 정밀도 비교, 새 모델 forward는 NOT_RUN이다.
- 현재 로봇 state·토크·온도·HOME 오차는 미확인이다. 로봇·serial을 열지 않았다. 중단 기록을 지우지 않았고, 마지막 원격 확인 이후 현재 파일 상태는 연결 불가로 재확인하지 못했다.
- live, ENABLE/LIVE 입력, 토크·목표 쓰기, 원본 데이터/체크포인트 변경, 기존 venv/LeLab 수정, 재학습, push는 NOT_RUN이다.

## [재개 묶음]

로컬 배포 폴더의 SOFTWARE_BUNDLE_MANIFEST.json에 코드 묶음·설치기 SHA256이 있다. 모델 가중치·원본 데이터·비밀번호·토큰·실제 접속 주소는 묶음에 없다.

`deploy_software_only.ps1`은 Target에 계정@주소 형태만 받는다. ssh 접두어·공백·비밀번호만 넣으면 실행 전에 거부한다. 암호를 저장하지 않고 SSH가 인증을 요청한다. 기존 파일을 덮어쓰지 않는 새 전송 폴더를 만든 뒤 배포한다.

설치기는 고정 경로 ~/nov_live_prep_20261104 아래만 배포한다. 파일 해시·경로·링크를 먼저 검증하고 기존 파일은 .bak_날짜로 보존한다. SERIAL_STOP.json, 모델, 원본 데이터, venv·인증 경로는 배포 대상이 될 수 없다. 기본값은 check-only이고, 배포 스크립트가 --execute를 명시한다.

배포 후 `resume_software_only.sh`는 관련 모의 테스트 → 파일 사전점검 → 저장 데이터 정밀도 비교 → 저장 예측 재생만 실행한다. sudo·서비스 조작·카메라·로봇·serial·live를 실행하지 않는다. GPU 비교가 실패해도 원인을 로그에 남기고 cached 재생으로 넘어간다.

연결 복구 후, 파일이 배포된 Jetson에서의 소프트웨어 재개 명령:

```bash
bash ~/nov_live_prep_20261104/resume_software_only.sh
```

이 명령은 아직 이번 세션에서 실행하지 않았다. 전송 전에는 파일이 없으므로 먼저 배포해야 한다. 현재 접속 대상은 이미 대화에서 받았으며, 연결이 복구되면 추가 승인 없이 이 범위로 계속할 수 있다.

## [ROLLBACK]

원본 저장소의 robot.py·설치 환경·원본 데이터·체크포인트는 바꾸지 않았다. 로컬 작업 브랜치에서 코드/문서를 커밋한다. Jetson은 전송 전에 막혀 이번 변경을 되돌릴 작업이 없다. 향후 배포 시 생성되는 SOFTWARE_DEPLOY 영수증의 대응 .bak_날짜 파일을 사용하며, 롤백으로 serial 중단 기록을 삭제하지 않는다.
