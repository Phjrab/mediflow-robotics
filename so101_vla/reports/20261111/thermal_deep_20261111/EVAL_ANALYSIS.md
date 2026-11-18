# 온도 84/34 및 기존 검증 가정 독립 분석

## 실제 설치 SDK 메모리 포트 재생 — exit 0

총괄의 명시적 추가 요청으로 `tests_actual_sdk_replay.py`를 생성하고 `python -B reports/thermal_deep_20261111/tests_actual_sdk_replay.py`를 실행했다. 3 tests/24 replay subcases PASS, **종료코드0**. 기존9tests는 반복하지 않았다. 실제 SDK 원본은 수정하지 않았고 전후 SHA256 동일하다. 아래 초기 분석의 “테스트 실행 없음”은 이 추가 요청 이전 이력이다.

실제 `protocol_packet_handler.readTxRx→txRxPacket→txPacket/rxPacket`을 실행하고, port의 readPort/writePort만 메모리로 대체했다. package namespace를 직접 구성해 serial 의존 PortHandler와 __init__를 import하지 않았다. serial/SSH/카메라/모터/추론 접근, 필터 추가 및 runtime 수정이 없다. 메모리의 TX는 READ주소63/폭1만 허용했다.

- 보존된 ID6의56/38/38/38 원문4개 모두 SDK에서도 그대로56/38/38/38, comm0/status0으로 해석됨.
- 같은4개를1/2/3byte 분할 readPort로 전달한12개 사례도 원값 그대로 해석됨.
- 각 원문 checksum 마지막byte를XOR1 손상하여1byte분할/일반수신한8개 사례 모두 `COMM_RX_CORRUPT=-7`,data=[]로 거부됨.
- 매회 SDK가 생성한 최종TX가 보존TX `ff ff 06 04 02 3f 01 b3`와 정확히 일치, timeout예상길이7, clearPort호출1, 종료후is_using=false 확인.

지문:

| 대상 | SHA256 |
|---|---|
| tests_actual_sdk_replay.py | 30e0ca30b9456c4f972dfc7b5b86d5ddba1693d28684e20b3fbc09f266009d3c |
| actual_sdk/scservo_sdk/protocol_packet_handler.py | 9932c85b9e2ac671a7e33f39e8e80b05d6592280d9f2bcebd85103845e36e655 |
| actual_sdk/scservo_sdk/scservo_def.py | 068f7ad5c453ad6e338543c706047253495fcd6bf9a632465501ace12fee0627 |

실제protocol파일 지문은 앞서 읽은11월6일source_review본과 동일하다. 이시험은 정상원문56이SDK숫자변환에서38로바뀌거나정상분할수신만으로56이생긴다는설명을 지지하지 않는다. 하지만 메모리재생은 원문56의 발생원인을 재현한시험이 아니다. 실제wire/장치센서/펌웨어/대기응답의진실성,물리serial시각·버퍼·OSdriver·다른ID소비,전체PacketPermission체인 검증은 포함하지 않는다. 임계55 gate 또는 현장오류가 해결됐다는 뜻이 아니다.

## 원문 JSON 독립 확인 추가 — 고속 개별 READ에서 56→38×3 확인

총괄이 전달한 두 JSON만 추가로 읽어 로컬 메모리에서 전체 TX/RX 헤더·ID·길이·주소·폭·8bit checksum·comm/status와 시간/phase를 검사했다. 새 runtime/시험 파일 생성 또는 장치 실행은 없다.

| 원문 파일 | SHA256 |
|---|---|
| thermal_rate_1794388240889594581.json | fc3423c8176281116dc6987a8712c903d3b12b88550bf7fb8e2b7099ddc9f72c |
| thermal_slow_1794388465742337366.json | 2026005af449c937d084bedc3bd905cc1a711ad1a04f2edf9174edd2d9c4f3eb |

두 파일 trace 801+480=1281건 모두 TX 8byte `FF FF ID 04 02 3F 01 checksum`, RX 7byte `FF FF 동일ID 03 00 data checksum`, 양쪽 합 검사 통과, comm/status=0이다. **기록된 전송 주소는 모두63/폭1**이며 잘못된 주소·응답 길이·단순 checksum 오류는 발견하지 못했다. JSON이 물리 wire의 독립 계측인지, SDK 전달 전/후 중 어느 시점에 복사됐는지는 logger 소스 없이 확정하지 않는다.

| 파일/phase | sample 주기 수 | raw 응답 수 | 관측 온도 | sample 간격 평균 | raw 기록 간격 중앙값 |
|---|---:|---:|---|---:|---:|
| rate / individual_only_20 | 60 | 360 | 34/35/38 외 gripper45 1회 | 50.107ms | 0.499ms |
| rate / individual_only_30 | 73 | 441 | 34/35/38 외 elbow36 1회, gripper56 1회 | 33.420ms | 0.502ms |
| slow / individual_only_1 | 5 | 30 | 34–38 | 1001.015ms | 10.700ms |
| slow / individual_only_5 | 25 | 150 | 34–38 | 200.210ms | 10.727ms |
| slow / individual_only_10 | 50 | 300 | 34–38 | 100.095ms | 10.681ms |

rate는20Hz60cycle,30Hz73cycle와56검출후3재독으로 종료했다. `error=RuntimeError('55C stop; no further phases')`이므로 후속phase를 시험한 것으로 해석하면 안 된다. slow는각phase약5초를5/25/50cycle,총80cycle/480응답으로 완료했고 error=null이다. raw간격은 인접trace 시각 차이로 cycle간 대기도 포함한다. 중앙값은 읽기 burst 차이의 지표이며 엄밀한 응답 latency/quiet시간 자체와 구별해야 한다. slow최대work는66.549ms로6축수집에약64–67ms, rate최대work는20Hz4.842ms/30Hz4.294ms였다.

rate마지막 raw index797(0부터)은 시각12820.906219666, ID6/gripper TX `ff ff 06 04 02 3f 01 b3`, RX `ff ff 06 03 00 38 be`다. data `0x38`=56, checksum190이며 합검사255를 만족한다. 그 뒤 **0.508687ms** 만에 같은ID/주소/폭의 RX `ff ff 06 03 00 26 d0`(38), 이어0.461997ms후38,0.461806ms후38이 기록됐다. 마지막sample도 gripper56을 보존하고 `recheck.gripper=[38,38,38]`를 병기한다. 재독으로56을 삭제한 로그가 아니다. 약0.5ms에18°C가 바뀌는 표시는 정적 온도 측정으로 의심할 강한 정황이지만 어느 단계의 값이 잘못됐는지는 단독 확정할 수 없다.

rate두phase sample.bulk는 모두 빈 객체, phase는individual_only, 기록된TX는전부63/1이다. 따라서 **이56/38이벤트에 직전56..70 full bulk읽기가 필수는 아니다.** “직전full15byte응답혼입만이원인”이라는 단독설명은 이 로그에 맞지 않는다. 과거run잔류/미기록별도통신까지 배제했다는 의미는 아니다.

두파일 goal_writes=0,port_closed=true,전체trace.queued=0을 확인했다. 목표쓰기/동작지시를 재현의 필수조건으로 보는 가설은 약해진다. queued=0은 측정시점의큐관측이며 장치측미송신·전송중·SDK가이미소비한packet·오해석을 배제하지 않는다. queued측정의시점/정의는logger구현확인이 필요하다.

결론: 고속 개별읽기에서도 형식정상인gross온도표시가재현했고, 저속·축간간격을둔이번시험에서는 같은gross가없었다. **cycle Hz와 축간간격 두조건이 함께변해30Hz자체를원인으로분리할수없다.** burst간격/요청방식/구현경로 의존성의 단서이며 통신/SDK/펌웨어/센서 중 하나를 확정한 증거는 아니다. 84/34자체는 이두파일에 없고56/38이라는별도gross재현이다.

총괄이 추가 전달한narrow63 sync20/30/50/60Hz·각8초·1277cycle/7662reply·최대47·HOLD없음은 **이담당이원문을읽지않은전달정보**로 구분한다. 47도34–38밖이므로 “HOLD없음=해결”은 아니다. 진행중full56+quiet10ms+sync63대조결과도 미수신이며 결론에 혼합하지 않는다. 요청방식/축간간격/cycle율/duration/표본수/축별분포/모든이상값을 함께 비교해야 한다.

아래 초기 분석의 자료수신대기 표현은 이 추가절 이전의 이력이다. 실제 설치SDK파일/logger소스는 여전히 독립 미수신 상태다.

2026-11-11 KST. 로컬 소스 읽기만 수행했다. runtime 수정, SSH, 장치, 추론, 테스트 실행은 없다. 이 문서는 현재 증거로 확정 가능한 코드 성질과 현장 확인이 필요한 가설을 구분한다. 실제 설치 SDK와 새 진단 로그는 총괄 전달 대기다.

## 판단

기존 9 tests 통과는 **온도 데이터의 출처·최신성·물리적 정확성 검증 통과를 뜻하지 않는다.** 테스트는 실제 SDK와 바이트 스트림을 우회하며, 실제 보호장치도 형식상 유효한 동일 ID/동일 길이 응답을 다른 주소·이전 요청의 응답과 구분할 정보가 없다. 따라서 개별 63 READ 채택만으로 gross 불일치의 근본원인이 해결됐다고 결론 낼 수 없다.

개별 읽기 55 이상 래치와 정책 중단은 별개의 안전 동작이다. 84를 읽고 HOLD하는 것은 해당 정책에 맞지만, 84가 현재 온도인지 판단해 주지는 않는다. 반대로 개별 34를 채택해 bulk 84를 무시하는 것이 데이터 신뢰성 관점에서 항상 옳다는 근거도 현재 모의시험에는 없다. 유효해 보이는 낮은 값의 오염 가능성도 동일하게 검토해야 한다. 안전 임계값을 완화할 근거는 없다.

## 검토 소스 지문

| 파일 | SHA256 |
|---|---|
| tmp/vla_direct_live_v3_20261109/stream_live.py | 91f83d1f9ce62941ba1819d8cba3c30b2f48e806074148bb757abfad645c97e0 |
| tmp/vla_direct_live_v3_20261109/thermal_read.py | 90dd90cf972aa5b89eac6edd2f2957e2ba7622af182ec0514c900ae020bb0c92 |
| tmp/vla_direct_live_v3_20261109/tests_thermal_read.py | 7511412f694ad78153352386484f6f3a6054b3d5e8c1e652de19c671e2ee1608 |
| reports/v6_process_isolation_20261105/operator_candidate/individual_reply_guard.py | d3b5916b6d0a6234af62a63c56a32fd56fb486eaf451e5ae083852e052e7cc94 |
| reports/goal_incident_20261106/source_review/protocol_packet_handler.py | 9932c85b9e2ac671a7e33f39e8e80b05d6592280d9f2bcebd85103845e36e655 |

마지막 SDK는 11월 6일 저장본이다. 현재 Jetson 설치본과 동일하다고 가정하지 않았다. PacketPermission는 로컬 `reports/live_safety_fix_20261105/live_safety_runtime.py`, direct runner는 `tmp/vla_direct_live_20261109/direct_live.py`를 읽었으며 실제 import 해석 경로는 설치본 확인이 필요하다.

## 패킷 생성과 검증 책임

1. thermal_read는 `readTxRx(port, mid, 63, 1)`을 호출한다. 하드웨어 레지스터 쓰기를 요청하지 않지만 READ 명령도 wire 송신은 발생한다. “쓰기 0”은 레지스터 쓰기/목표 전송 0이라는 의미로 써야 한다.
2. 저장 SDK readTxRx는 8바이트 0 배열에 ID, length=4, instruction=2, address, width를 넣는다. txPacket이 header FF FF와 마지막 체크섬을 채운 후 clearPort/writePort를 호출한다. 예시 ID6/주소63/폭1의 정상 송신은 `ff ff 06 04 02 3f 01 b3`이다. 이 예시는 코드 산술이며 현장 캡처가 아니다.
3. StrictIndividualReplies는 txRxPacket 입구를 감싼다. ID 1..6, instruction READ, request 길이8, width 1/2/4를 검사한다. **주소63만 허용하는 guard가 아니다.** request header/length byte/checksum도 여기서는 완성 wire 형식으로 검사하지 않는다. SDK가 아직 header/checksum을 채우기 전이므로 이 점 자체를 wire 결함으로 단정해서는 안 된다.
4. 완성된 송신 바이트의 헤더/길이/checksum/주소·폭 whitelist는 별도 PacketPermission.inspect가 검사한다. 읽은 Permission에는 63:1 외 40:1,65:1 등도 존재한다. DirectIO는 33:8/56:15를 추가하고 StreamingIO는 67:2 및 그리퍼 읽기를 추가한다. 따라서 broad whitelist는 특정 호출이 **바로 지금** 63을 읽는다는 호출별 증명을 대신하지 못한다. 실제 설치/동시 guard 체인과 전송 로그 확인이 필요하다.
5. 응답은 header, ID, length, status, data, checksum이다. SDK rxPacket의 재동기화·checksum 처리 후 StrictIndividualReplies가 ID/폭·길이/헤더/상태/checksum을 다시 검사한다. helper는 decode data의 길이1, int 범위0..255와 communication/status를 검사한다. 이 층들은 형식 검증을 강화하지만 동일 wire에 대한 독립 센서 측정은 아니다.

## 검증해도 분리할 수 없는 경우

| 상황 | 현 구조의 판별 가능성 |
|---|---|
| 잘못된 ID/길이/헤더/0이 아닌 상태/단순 checksum 파손 | guard에서 거부 가능 |
| 같은 ID·폭의 예전 정상 응답이 늦게 도착 | 응답에 timestamp/sequence가 없어 최신 요청 응답과 구분 불가 |
| 같은 ID의 다른 1byte 주소 응답 | 응답에 주소가 없어 해당 데이터가 63인지 응답만으로 구분 불가 |
| 형식·checksum 정상인데 센서/펌웨어가 잘못된 값 반환 | 소프트웨어 형식 검사만으로 물리 진실성 판단 불가 |
| checksum을 보존하는 복수 바이트 손상 | 8bit 합 검사만으로 모두 검출 불가 |
| bulk와 개별 모두 같은 오염값 | 불일치가 없어 재측정하지 않음; 공통 오류 검출 불가 |
| 개별 첫값과 재측정이 다른 경우 | max 보존으로 낮은 재측정이 높은 첫값을 지우지 못함. 어느 값이 진실인지는 판정하지 않음 |

현재 코드는 `sum(raw[2:]) % 256 == 255`라는 8bit additive checksum을 사용한다. 강한 CRC가 아니며 tests의 fault 이름 `crc`는 실제로 마지막 체크섬 바이트를 XOR하는 단순 손상이다. SDK와 guard가 같은 합 검사를 두 번 해도 독립된 오류 검출 부호 두 개가 생기는 것은 아니다.

코드 산술 예시: ID6·status0·1byte 데이터 84는 `ff ff 06 03 00 54 a2`, 34는 `ff ff 06 03 00 22 d4`로 각각 정상 합 검사를 통과한다. 어느 쪽도 형식만으로 “거짓 온도”라고 판단할 수 없다. 84→34와 checksum 반대 방향 변화를 포함한 2바이트 손상은 합을 보존할 수 있다. 이는 가능성 설명이며 실제 손상이 있었다는 증거는 아니다. bulk에서는 data+50/다른 data−50도 같은 합을 유지할 수 있다.

`StrictIndividualReplies.history`는 guard 객체 내부에만 있고 thermal_read는 이를 반환·로그하지 않는다. 현재 disagreements에는 decoded 온도만 남고 원시 individual 응답은 남지 않는다. 동일 source disagreement가 반복되면 로그도 내용 변경 때만 기록한다. 따라서 과거 decoded 84/34만으로 실제 wire에서 무엇이 달랐는지 역추적할 수 없다.

## 9 tests가 이번 오류를 잡지 못한 구체적 이유

- fake Handler.readTxRx가 직접 만든 요청을 fake txRxPacket에 전달한다. 실제 SDK txPacket/header/checksum 생성, rxPacket의 readPort 조각·timeout·header 탐색·ID 건너뛰기·clearPort 동작을 전혀 실행하지 않는다.
- fake 응답은 제공된 숫자를 data로 넣고 그 숫자에 맞춰 checksum을 새로 계산한다. 즉 84를 넣으면 “정상 84 응답”이 만들어진다. 테스트에는 실제 84/34 현장 raw packet이나 전송 과정이 없다.
- 검증 사례는 checksum 단순 파손, ID 오류, 길이 오류, status, communication, exception이다. checksum 보존 손상, 같은 ID·폭 stale 응답, 다른 주소 응답, split/concatenated/interleaved 수신, TX bytes와 기대 주소 불일치, 바쁜 포트 및 실제 permission 체인은 다루지 않는다.
- AST Parent.feedback는 bulk 62/status0/raw100을 상수로 반환한다. 실제 56..70 bulk 파싱과 팔꿈치 raw feedback 진실성은 검사하지 않는다. `feedback_bytes`도 15바이트 0이며 실제 current decode 검증이 아니다.
- 온도 gate도 실제 base.feedback_gate 대신 max>=55 함수로 대체한다. 실제 motor status 통합 검증과는 범위가 다르다.
- hold 테스트는 실제 hold 메서드를 추출하지만 read_modes와 write_start는 lambda다. hold 요청값 생성과 wire_fault 선차단을 확인하며, 실제 SDK sync_write/완성 packet/Permission/모터 수신을 증명하지 않는다. Handler.writes는 송신계층 계측이 아니라 초기값0으로 남는 카운터다. 별도 hold_writes가 소프트웨어 호출 여부를 기록한다.
- bulk62/individual38 fixture는 **입력으로 가정한 상황에서 개별값을 채택함**을 증명한다. bulk가 틀리고 individual이 맞다는 독립적인 ground truth가 아니다.

따라서 9 tests는 분기·래치·예외 정책 회귀검사로 유효하다. 실제 SDK/전송·센서 오류 재현검사로 확대 해석한 것이 잘못된 검증 가정이다.

## 팔꿈치 추종 분석에 주는 제한

온도 개별 읽기 강화는 팔꿈치 position feedback의 bulk 출처를 바꾸지 않는다. direct feedback의 present_raw는 같은 56..70 block offset0:2에서 해석한다. 그러므로 온도 오류의 원인이 bulk 수신 출처/바이트 오염이라면 팔꿈치 추종 오차도 독립검증이 필요하다. 반대로 두 문제가 같은 원인이라는 증거도 없다.

모델 목표, clipping 이후 실제 wire 목표, 모터에 남은 active target 주소67, present position 주소56을 동일 ID·시간축으로 비교해야 한다. 현재 로그의 sent/normalized 목표와 의심되는 bulk present만 비교해서 실제 기계 추종 결함·보정 오류·제어 실패를 확정할 수 없다. 목표 전송 간격이 바뀌는지 판단할 때 6~12회 추가 READ 지연도 확인 대상이나, 이번 소스 읽기만으로 타이밍 원인이라고 결론 내릴 수 없다.

## 다음 증거와 시험 요구 — 현재 실행하지 않음

총괄이 제공할 실제 SDK 파일·설치 import 경로/sha256, PacketPermission 설치본, 포트 clear/read/write 구현, 새 진단로그를 기준으로 위 구버전 소스와 비교해야 한다. 진단에는 monotonic 시간, 요청 ID/address/width, 최종 TX bytes, 원시 RX bytes, SDK comm/status, data decode, bulk 원시 전체/동일축 개별 응답, 진행 중 다른 READ 및 포트 소유 정보가 필요하다. 장치 접근 여부는 총괄의 승인 범위에 따른다.

후속 장치 없는 시험은 실제 설치 SDK를 fake byte-stream port에 연결해 정상 요청 bytes, checksum 보존 손상, stale same-ID/width, 다른 ID 선행 packet, partial/합쳐진 readPort, duplicate header, timeout 후 다음 요청, whitelist 어긋남을 주입해야 한다. 포맷상 식별 불가능한 사례는 “거부 PASS”를 기대해서는 안 되며 **구별 불가능함을 명시한 제한**으로 기록해야 한다.

기존 현장 사실은 저장62/재측정38 재현 fixture와 readonly20round(1도 차이 2회, gross 재현 없음)로 분리한다. 새 84/34의 원시 증거는 아직 이 담당이 받지 않았다. 주소56+offset7=63이므로 단순 offset 오류라는 기존 추정은 근거가 없다. 펌웨어/전송/오래된 응답/SDK/센서 원인 중 어느 하나도 현재 확정할 수 없다.

## 총괄 전달 상태

### 추가 현장 정보와 속도 가설

총괄 전달에 따르면 사용자의 “10번 중 9번”은 **실기 성공 경험**을 뜻하며 위 9개 소프트웨어 모의 테스트와 전혀 다른 근거다. 사용자가 만졌을 때 뜨겁지 않다는 관찰도 전달됐다. 이는 순간 84°C 표시와 함께 분석할 유용한 정황이지만 접촉 위치/시각/내부 센서와 표면 온도 차이가 알려지지 않아 실제 내부 온도를 단독으로 확정하지는 못한다. 보고에서 사용자 경험을 모의테스트 통과로 바꾸거나 반대로 해석하지 않아야 한다.

총괄이 확인한 실제 SDK 정보: clearPort는 `ser.flush()`로 출력 완료를 기다릴 뿐 입력 버퍼를 비우지 않는다. 따라서 “매번 clearPort하므로 이전 응답이 제거된다”는 가정은 성립하지 않는다. same-ID/width 이전 응답 가능성을 검토할 이유가 강화되지만 실제 stale 응답 발생 증거는 아직 아니다. 설치 rxPacket도 8bit checksum/ID·길이 검증이라는 확인을 전달받았다. 실제 파일 및 raw 로그를 독립 수령한 상태는 아니다.

전달된 비교 조건은 Teleop20Hz, VLA50/60Hz, 모델 phase 약9.9 action/s다. **모델 phase 진행률과 wire 전송률은 다르다.** 9.9 action/s를 serial 9.9Hz라고 해석하면 실제 부하를 잘못 계산한다. VLA feedback 한 주기마다 bulk READ와 6개 개별 READ가 있고, 불일치 축마다 한 번 추가되어 개별6~12회가 된다. 단, 실제 전체 호출빈도는 초기냉각/추론대기/hold 등 경로와 지연을 함께 측정해야 하며 단순히 목표 전송Hz를 곱해 실측값으로 발표하면 안 된다.

속도 관련 가능한 설명은 요청/응답 빈도 상승, 수신 잔여/timeout 경계, SDK 재동기화, 읽기 지연에 따른 feedback 시점과 목표 시점의 어긋남, 제어 fallback이다. 현재 증거로 기계적 속도 문제나 특정 주파수 결함을 확정하지 않는다. 총괄의 READ-only rate 비교가 낮은/높은 조회율에서 raw 정상·오류 분포와 주기 지연을 보여줄 수 있으나, 목표 쓰기·모터 부하가 없는 시험은 동작 중 원인 전체를 재현하지 못한다. 오류가 안 나와도 실기 해결로 선언할 수 없다.

최종 표현은 “9개 모의테스트로 래치/중단/HOLD 분기 회귀를 확인했으나, 84/34 현장 오류의 재현·원인·해결은 미확인”이어야 한다. 기존 실기 성공 9/10은 유의미한 사용자 관찰로 별도 기록하며 단일 문제 해결 보증으로 확대하지 않는다.

초기 핵심 한계는 총괄에 메시지로 전달했다. 실제 SDK와 새 raw 진단 증거가 도착하면 이 문서 수정 전 백업 후 증거 기반 추가 분석을 진행한다. 안전 55 gate, 목표/토크/보정/런타임은 수정하지 않았다.
