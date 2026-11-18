# VLA 실행기 담당 결과

## 완료내용
- 기존 시작 위치 조건을 순수 모듈로 분리. elbow_flex 상한에만 1틱 측정 여유, 다른 축 0, type(value) is int 조건을 보존했다.
- live_start.validate_position은 순수 검사에 위임하고 ValueError를 기존 Stop으로 변환한다. 누락 축도 Stop으로 안전 차단한다.
- 오류에는 axis/current/command_low/high/start_low/high/margin/excess_ticks/reason을 포함한다.
- 읽기 전용 exporter CLI 구현. calibration/home/base 인자를 받아 입력을 read_bytes로만 읽고, base는 AST literal ELBOW_MAX만 추출한다. 원본 Python import/실행 없음. stdout JSON만 출력한다.
- 승인된 schema_version1 계약 적용. 홈 단위 degree 5축, gripper percent_0_100. tolerance=2, alignment_is_full_readiness=false. 파일명/역할/실제 SHA256 포함.

## 변경파일
- tmp/vla_direct_live_v3_20261109/live_start.py
- tmp/vla_direct_live_v3_20261109/start_position_rules.py (신규)
- tmp/vla_direct_live_v3_20261109/export_start_criteria.py (신규)
- tmp/vla_direct_live_v3_20261109/tests_start_position_rules.py (신규)
- live_start.py.bak_타임스탬프 백업 생성 완료.

## 검증결과
명령: python tmp/vla_direct_live_v3_20261109/tests_start_position_rules.py
결과: Ran 6 tests, OK.
- 6축 하한-1/하한/상한/상한+1/상한+2를 기존 수식과 비교.
- elbow 3082/3083 통과, 3084 차단(excess_ticks=1).
- NaN/None/bool/정수형 float/소수/누락 차단.
- invalid arm 진입시 Goal_Position/Torque_Enable 포함 write_start 총0.
- AST가 부작용 코드 실행 없이 상수를 추출. bool/수식/중복상수/누락은 거부.
- exporter 쓰기 함수를 금지한 mock 검사 통과. 3087 보정상한을 3082 command,3083 start로 반영. 원본 tolerance_deg5 대신2. source hash 일치. 추가축 거부.
- 최초 tempfile 기반 검사는 Windows sandbox temp 권한 때문에 실패해, 모든 파일 쓰기를 금지하는 인메모리 read fixture 검사로 교체했다. 최종6개 모두통과.

## 남은문제
- 로컬에 실제6축 Jetson 보정JSON이 없어 실파일 exporter 실행은 총괄이 수행해야 한다. exporter 입력은 정확히6축 key와 range_min/range_max 형태(LeRobot calibration)이다.
- UI/독립 평가 통합 결과는 각 담당이 확인한다. 배포/실기/SSH/serial/카메라/모델추론/서비스변경은 수행하지 않았다.
- 제어 inner 목표 상한3077, 온도/토크/동작 조건은 수정하지 않았다. 위치 조건 통과는 전체 준비완료가 아니다.

## 총괄요청
- 새 start_position_rules.py도 live_start.py와 같은 폴더에 함께 배포해야 한다.
- exporter 실행: python export_start_criteria.py --calibration <실제6축보정JSON> --home <홈JSON> --base <실제base/direct_live.py>
- JSON stdout을 통합 담당 경로에 저장하고 UI에 연결. 새 스키마 소비 및 독립경계검사 후 총괄만 배포.

## 통합검토 후 최소수정
- HOME_TOLERANCE=2.0은 start_position_rules.py 단 한 곳에 두고 live_start.py와 exporter가 동일 상수를 import하도록 통일.
- 세 코드 파일 수정 전 .bak_타임스탬프 백업 완료. 기존 모터 판정/허용 범위 변경 없음.
- 동일6개 테스트 재실행: Ran 6 tests, OK.
- 최종 SHA256: live_start.py 2b4760a9847fcf37cbb6557f14e9cb3e0e4cb9258b33cb4d64519b2a54d0d466; start_position_rules.py 0b54c4a7850b038cf6c4a974d23cab351abb9fd7e7fad152541efffcea15a7fa; export_start_criteria.py 873c65982e171ba36aab99d28b1dd95c056c82fe7a447a436ad49be6bf49558a.

## 온도 불일치 추가 배정 결과
### 완료내용
StreamingIO.feedback는 bulk 온도를 temperatures_bulk에 보존하고, StrictIndividualReplies를 통한 6축 개별 주소63/1byte READ 값을 안전판정에 사용한다. 불일치축은 추가1회 읽어 더 높은 개별온도를 채택한다. thermal_source_disagreement는 최초/내용변경 때만 기록한다. 모든 통신/검증 실패는 wire_fault=True와 Stop으로 차단한다. 개별>=55는 세션에 래치하여 후속cool 값이나 시작냉각경로로 지워지지 않는다. 시작50/45,실행55 수치와 모델/토크/보정은 그대로다.
### 변경파일
- tmp/vla_direct_live_v3_20261109/stream_live.py (사전타임스탬프백업)
- tmp/vla_direct_live_v3_20261109/thermal_read.py (신규)
- tmp/vla_direct_live_v3_20261109/tests_thermal_read.py (신규)
### 검증결과
- python -B tmp/vla_direct_live_v3_20261109/tests_thermal_read.py: 8 tests OK. 실 StrictIndividualReplies + fake SDK로 CRC/ID/length/status/communication/수신예외, 6축상이값, bulk62/direct38, direct55후38을 검증. 실제feedback메서드를 AST로 독립클래스에 넣어 wire_fault/래치/진단보존/로그중복방지 확인. 모든모의쓰기0.
- PYTHONPATH=tmp/vla_direct_live_20261109 에서 python -B tmp/vla_direct_live_v3_20261109/stream_live.py --mock: 기존10 tests OK.
- SHA256 stream_live.py aaa8f1b4cb824e7c368de57ab6b58d451fb6a1d523ede3f7398662ab11a746b5; thermal_read.py 90dd90cf972aa5b89eac6edd2f2957e2ba7622af182ec0514c900ae020bb0c92; tests_thermal_read.py 0b06d83439c19647ab3c43c73cf47991c99b7eda0d8033fd5f900701650992c5.
### 남은문제 / 총괄요청
- 매feedback마다6~12개 READ 추가되므로 실제읽기지연/주기영향은총괄확인필요. 이담당은SSH/실장치/추론실행없음.
- 배포시 thermal_read.py를 stream_live.py와 같은폴더에 포함한다. base direct_live.py는 수정하지않았다.

### 온도 리뷰 반영 최종본 (위 즉시gate/시작과열래치 설명 대체)
- feedback는 수집과 motion_ready=True 실행중 온도>=55래치만 수행한다. 실제 gate는 checked에서 래치값을 합친 guarded sample로 수행한다. except에서도 기존래치를 보존한다.
- 초기 motion_ready=False에서는 기존50/45 냉각대기를 유지한다. 초기55→44는 정상냉각종료하고 과열래치를 만들지 않는다.
- 실행55→38은 checked에서 계속차단하지만 feedback 자체는 읽기가능하여 기존 hold()의현재위치특별 Goal_Position 전송은가능하다. 통신오류 wire_fault=True이면 HOLD쓰기까지차단한다.
- 전용9 tests OK: 실제 feedback/checked/hold AST독립모의로 과열후HOLD1회/후속정책차단/초기55냉각/통신오류HOLD쓰기0 확인. 기존 --mock10 tests OK.
- 최종 stream_live.py SHA256 91f83d1f9ce62941ba1819d8cba3c30b2f48e806074148bb757abfad645c97e0; tests_thermal_read.py 7511412f694ad78153352386484f6f3a6054b3d5e8c1e652de19c671e2ee1608; thermal_read.py는90dd90cf972aa5b89eac6edd2f2957e2ba7622af182ec0514c900ae020bb0c92 그대로.

## 개별 온도 원문 계측 보강 (원인해결 아님)
- thermal_read.py와 tests_thermal_read.py 수정전백업완료. stream_live.py는수정하지않음.
- 불일치축 또는첫개별값>=55인축의 reply_trace에 StrictIndividualReplies.history의 address/expected_id/expected_bytes/raw_hex/communication/status를복사한다. 매READ직전getBytesAvailable()을시도하여queued_before_read에보존,API없음/조회실패는null. RX를소비/삭제/flush하지않는다.
- first>=55가bulk와같아도trace보존하며추가READ수는변경하지않는다. 불일치때만기존대로추가1회,max채택. 임계55/래치/초기50/45/HOLD/통신오류차단그대로.
- history의변동unix시각은복사하지않아동일원문/동일큐상태일때기존첫/변경로그정책을보존한다. strict context를떠나도복사된원문은유지된다.
- tests_thermal_read.py11개OK, stream_live.py --mock기존10개OK. 실guard+fakeSDK raw gripper56 FF FF06 03 00 38 BE →38 FF FF06 03 00 26 D0,큐0보존,bulk와동일한55도추가읽기없이기록,실feedback로그원문보존/56래치정책차단/현재HOLD가능/통신오류쓰기0 확인.
- SHA256 thermal_read.py 154b0b8593aff1b433d0bb1009fb9aedbf8574cde7456a175539876873cff33b; tests_thermal_read.py a7fe26c22a5ee514d79848aa66b8ecd8d62fa13d3aafa0ced7b8bea3276f72fe. stream_live.py는91f83d1f9ce62941ba1819d8cba3c30b2f48e806074148bb757abfad645c97e0로불변.
- SSH/장치/추론/live접근없음. 이는계측보강이며물리실기이상원인을해결하지않는다. SDK내부에서소비된다른ID프레임까지포함하는전체바이트trace는아니다.
