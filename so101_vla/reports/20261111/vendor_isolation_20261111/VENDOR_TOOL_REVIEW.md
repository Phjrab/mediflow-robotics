# 제조사 FD / Python SDK 읽기 경로 검토

2026-11-11 KST. 웹 문서·소스 읽기만 수행. 이 보고서만 생성했다. 실행파일 다운로드/실행, SDK 설치/실행, SSH·실장치 접근, 런타임 수정 없음. C++ SDK 상세 검토는 총괄 담당이라 중복하지 않았다.

## 결론

제조사 FD는 Windows용 별도 GUI 검사 경로다. 공식 출처와 연결/검색/상태 읽기 기능은 확인했지만, 공개 문서만으로 해당 폐쇄형 실행파일의 Open/Search/대상 선택이 자동 토크·설정 쓰기를 전혀 하지 않는다는 패킷 수준 보장은 못 했다. 따라서 제조사 프로그램이라는 이유만으로 읽기 전용이라고 확정하면 안 된다.

공식 Python SDK는 코드상 PortHandler 생성·호스트 baud 설정·Ping·ReadPos·read1ByteTxRx(ID,63)를 쓰기 API와 구분할 수 있다. 다만 기존 실행기와 같은 계열의 SDK/파서이므로 독립 제조사 GUI 검사와 구분해야 한다. 신판은 RX clearPort 구현도 다르다. SDK 없는 raw 재현이 이미 존재하므로 다른 버전 SDK에서 짧게 정상이라는 결과를 원인 수리로 부를 수 없다.

## 1. 배포 출처와 지원 환경

| 도구 | 확인한 공식 출처 | 환경 / 출처 판단 |
|---|---|---|
| FD | [제조사 중국어 software 페이지](https://www.feetech.cn/software.html) | FD1.9.8.2 오프라인/1.9.8.3 및 FD 다운로드 링크가 실제 목록에 있음. 국제 software.html은 이번 조회에서 timeout. 실행파일 직링크를 추정하지 않음 |
| FD 설명 | [제조사 Wiki FD](https://ftservo.github.io/ftservo-wiki/tools/fd/) | Windows 도구라고 명시. COM/현재 baud/Open/Search/검색 중지/대상 선택 순서를 안내 |
| 공식 Python SDK | [제조사 Wiki Python](https://ftservo.github.io/ftservo-wiki/sdk/python/) → [ftservo/FTServo_Python](https://github.com/ftservo/FTServo_Python) | Wiki가 공식 저장소로 직접 링크. Windows/Linux/macOS의 가상환경·포트 경로 안내가 있음. 실제 보드/드라이버별 동작은 미실행 |
| 현행 공식 SDK 목록 | [제조사 다운로드 Wiki](https://ftservo.github.io/ftservo-wiki/downloads/) | Python/FTServo_Python, Linux C++/FTServo_Linux를 직접 링크. 따라서 GitHub ftservo 출처는 제조사 자체 페이지에서 연결 검증됨 |
| 예전 SCServoSDK | [Gitee ftservo/SCServoSDK](https://gitee.com/ftservo/SCServoSDK) | FTServo 계정의 여러 MCU용 SDK 묶음. GUI FD 자체와 동일하지 않음. 임의 GitHub mirror/PyPI 이름만으로 공식 배포물과 동일하다고 하지 않음 |

제조사 중국어 사이트가 ftservo.github.io Wiki를 직접 링크하고, Wiki가 SDK 저장소들을 연결하므로 공식 출처 연쇄를 확보했다. FD 소스/서명·실행파일 해시까지 확보한 것은 아니다. [공식 사이트의 자료 링크](https://www.feetech.cn/software.html)

## 2. FD: 확인된 UI 단계와 확인되지 않은 무쓰기 보장

공식 설명의 흐름은 COM 선택 → 장치 현재 baud 선택 → Open → Search → 검색 중지 → 대상 선택이다. ID/서보 baud 변경은 별도 수정/저장/재시작 절차이고, 파라미터 변경·동작 시험도 별도로 설명한다. 1M를 연결 화면에 선택하는 일과 모터 EEPROM baud를 바꾸는 일은 다르다. [공식 FD 연결·설정 안내](https://ftservo.github.io/ftservo-wiki/tools/fd/)

공식 기존 튜토리얼의 검색 색인 본문은 FD 피드백에 온도(℃), 현재 위치, 전압, 이동·상태를 표시한다고 설명한다. 원문 PDF 직접 열기는 timeout이라 그림의 정확한 탭/버튼 위치와 버전 차이는 확인하지 못했다. [제조사 상세 입문 PDF](https://www.feetechrc.com/Data/feetechrc/upload/file/20220618/%E5%85%88%E7%9C%8B%E8%BF%99%E9%87%8C-%E5%85%A5%E6%89%8B%E6%95%99%E7%A8%8B2-1.pdf)

현재 확인 수준에서 FD로 허용 가능한 안내 범위는 다음과 같다. 이는 실제 버튼 실행 지시가 아니라 총괄의 검토 기준이다.

1. 장치를 연결하지 않은 상태에서 공식 FD 배포물의 버전/화면/출처를 확인하는 준비 단계.
2. 실제 연결은 총괄이 포트 단독 소유·토크 OFF 상태와 해당 버전의 동작을 확인한 뒤 진행. Open/Search가 제조사 문서상의 연결/발견 기능이라는 사실과 숨은 쓰기0이 검증됐다는 주장을 분리.
3. 검색 후 멈추고 알려진 ID1–6을 선택하여 피드백의 현재 위치/온도만 관찰하는 경로가 후보. 별도 Read/Refresh가 있다면 정확한 버전의 실제 화면을 확인한 후 단계 하나씩 안내. 미확인 버튼 이름/자동 스캔 토크 동작은 추정하지 않음.
4. 설정/속도·가속도 Set, 슬라이더, Torque, ID/baud 변경, Program/Save, Calibration/Offset/Reset, Upgrade/Online update와 Test/Sweep/동작 버튼은 이번 읽기 검사에서 제외.

공식 문서는 Search와 변경 절차를 구분하지만 Search의 실제 opcode 목록, 초기 자동 토크 쓰기 부재, 연결 때 기본값 적용 부재는 제시하지 않는다. 엄격한 쓰기0을 입증하려면 해당 바이너리의 검토 가능한 소스 또는 제조사 확인/독립 TX 관측이 추가로 필요하다. 현재의 결론은 미확인이지 쓰기를 발견했다는 뜻은 아니다.

## 3. Python SDK: 함수별 실제 코드 검토

| 단계 | 소스 근거 | 서보 레지스터 쓰기 판단 |
|---|---|---|
| PortHandler 생성 | port_handler.py의 __init__ | 변수 초기화만, 통신 없음 |
| sms_sts 객체 생성 | sms_sts.py:56–58 및 GroupSyncWrite.__init__ | 파서/동기쓰기 버퍼 객체 생성. 객체 이름에 Write가 있어도 생성만으로 송신하지 않음 |
| openPort / setBaudRate(1M) | port_handler.py:21–45,84–102 | 호스트 serial 설정/open·close/reset_input_buffer. 모터 register6 WRITE 아님 |
| 알려진 ID ping | protocol_packet_handler.py:237–255 | opcode PING 후 모델주소3/2byte READ. 토크/ID/baud WRITE 없음 |
| 현재 위치 | sms_sts.ReadPos(ID) | 56/2byte READ + 부호 디코드 |
| 현재 온도 | read1ByteTxRx(ID,63) | READ opcode2,주소63,폭1. 온도 쓰기 없음 |
| 현재 토크 확인 | read1ByteTxRx(ID,40) | register40을 읽는 것이며 0/1을 쓰는 호출과 다름 |
| 종료 | closePort | host serial close만 |

[PortHandler](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/port_handler.py), [sms_sts](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/sms_sts.py), [protocol_packet_handler](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/protocol_packet_handler.py), [GroupSyncWrite](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/group_sync_write.py)

제조사 sms_sts/ping.py 전체 경로는 port open/host baud1M/ping ID1/model 표시/close이며 목표·토크·설정 쓰기 호출이 없다. 샘플의 “change baudrate” 출력은 호스트 UART 변경이다. 다수 ID 검색이 필요해도 알려진 ID를 바꾸어 이 Ping만 반복하는 방식과 GUI의 일반 Search는 구분된다. 임의 다른 auto-config/scan 스크립트의 ID/baud 저장 부재까지 이 샘플로 증명할 수 없다. [공식 ping.py](https://github.com/ftservo/FTServo_Python/blob/main/sms_sts/ping.py)

**read_write.py는 읽기 전용 예제가 아니다.** 시작 후 WritePosEx(ID1,4095,60,50)와 WritePosEx(ID1,0,60,50)를 반복한다. 이 파일이나 WritePos/WheelMode/WriteSpec/LockEprom/unLockEprom/RegAction/동기쓰기 예제를 온도 확인용으로 실행하면 안 된다. [공식 read_write.py](https://github.com/ftservo/FTServo_Python/blob/main/sms_sts/read_write.py)

온도63/1byte와 현재 위치56/2byte는 공식 STS 표의 read-only 피드백이며40은 read/write 제어 레지스터다. read1ByteTxRx로40을 읽는 것만으로 토크 상태를 변경하지 않는다. [공식 STS 메모리 표](https://ftservo.github.io/ftservo-wiki/reference/parameter/memory-sts/)

## 4. 읽기 전용으로 구성 가능한 Python 순서

코드 검토된 최소 호출만으로 구성한다면: 호스트 포트 설정 → 알려진1–6 Ping/model READ → register40 READ로OFF 확인 →56 READ 현재 위치·63 READ 온도/통신결과·오류 기록 → finally close. 어떠한 write* / WritePos* / RegAction / torque enable / EEPROM lock 호출도 포함하지 않는다. Ping 성공은 온도 센서의 신뢰성이나 기구 안전을 증명하지 않는다. 아직 이 순서의 실제 실행 도구를 작성하거나 실행한 것은 아니다.

현 SDK는 기본으로 raw7byte 엄격폭 검증/모든RX 증거 보존/55정지 래치/배타 포트 검증까지 우리 요구를 모두 제공한다고 보장할 수 없다. 특히 예제는 일반 종료만 있고 모든 예외 경로 finally 정리를 갖추지 않는다. 실제 실행 전 별도 검사 harness에서 통신/장치error와 raw 기록·닫힘·쓰기0을 검증해야 한다. 이 보고서는 실행 승인이나 현 코드 교체 요청이 아니다.

## 5. 독립성의 정확한 수준

- FD GUI/Windows 검사는 현재 Jetson LeRobot 실행기와 다른 호스트 프로그램/환경의 제조사 교차 검사 후보다. 하지만 같은 모터·센서·케이블·어댑터를 유지하면 그 요소들은 분리되지 않는다. 프로그램 소스 독립성이나 센서 실온도 독립 측정도 아니다.
- 공식 Python SDK 재검사는 제조사 코드 판본 대조다. 기존 scservo 계열과 공유하는 프로토콜/파서 구조가 있어 완전히 독립된 통신 구현으로 부를 수 없다.
- 조사 시점 FTServo_Python의 clearPort는 reset_input_buffer()다. 총괄이 확인한 설치판의 flush()와 다르므로 정확한 commit/모듈경로/hash를 남겨야 한다. 이 차이를 임의로 런타임에 적용하지 않았다. 입력 배출은 잔여 증거를 없앨 수도 있다. [현행 port_handler.py](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/port_handler.py#L28)
- 이미 SDK 없는 raw READ/토크OFF에서도 이상값이 있었으므로 제조사 SDK나 FD에서 잠깐 정상인 것은 재현 조건 차이일 수 있다. 원인이 고쳐졌다는 결론에는 충분하지 않다.

## 총괄 전달 사항

공식 FD 다운로드 출처는 확보됐으므로 국제 사이트 timeout 때문에 비공식 mirror로 대체할 필요가 없다. FD가 제공하는 모든 기능을 허용하는 대신 읽기 관찰 후보와 숨은 쓰기 검증 한계를 명시해야 한다. 제조사 Python에서는 검토된 Ping/READ만 실행 경로에 포함할 수 있지만 기존 SDK와 독립성은 제한된다. C++ SDK 세부 검토/실제 실행은 총괄이 이어간다. 케이블 이동은 총괄이 현장 상태를 확인하며 한 단계씩 안내하고, 이 보고서에서 새 결선·토크 조작을 병행하도록 지시하지 않는다.
