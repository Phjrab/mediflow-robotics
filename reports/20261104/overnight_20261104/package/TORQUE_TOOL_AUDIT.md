# 토크를 켜거나 유지할 수 있는 경로

원격 설치 소스16개와 bash기록의 관련 항목167개를 파일 읽기로 조사했다. 전체 shell history나 인증정보는 저장하지 않고 줄번호·스크립트명·주요 플래그만 남겼다. history 출현은 해당 명령의 성공 실행이나 현재 토크 상태를 증명하지 않는다. 상세 목록은 reports/INVENTORY.json이다.

| 도구/경로 | 토크를 켤 수 있는 경로 | 종료 뒤 토크 | 근거 |
|---|---|---|---|
| 기존 torque_so101.py / reviewed | arm의 명시적 Torque_Enable=1. 현재 위치 Goal을 먼저 쓰는 구간에서도 재활성화가 관측됨 | disconnect(False)이므로 기존ON 유지 | 해당도구의 Torque_Enable/Goal_Position/disable_torque_on_disconnect 항목, 이전 실제 진단 로그 |
| guarded_read_v2 | 허가된현재목표쓰기와명시적ON. 읽기는개별READ | 종료 시 기존상태 유지 | 원격 source matches 및해시 |
| verified_goal_v3 | 기존목표=현재위치일때만명시적ON1회. Goal쓰기는금지 | ON 유지 | 원격v3, 이전 TORQUE_ON_HOLD 실제실행 |
| v4 dry | bus.connect(False)와관측만. send_action분기 미실행 | 이미켜져있던토크를끄지않음 | v4:401,420~440,close:294~315 |
| v4 live | send_action으로Goal변경. 예외/Ctrl+C때Goal HOLD시도 | disconnect(False) | v4:297,314,518 |
| 기존 home_align_web_reviewed | 위치/토크/온도읽기만. 토크ON명령 없음 | disconnect(False)로기존상태유지 | source matches와읽기루프. 이번새모니터는ON관측즉시중단 |
| LeLab record.py | robot.connect()가정상로봇연결/설정경로를사용 | 실행config에따름. 무조건OFF라고보장할수없음 | 설치record.py:741 |
| SOFollower.connect/configure/calibrate | configure및torque_disabled문맥에서토크순환가능 | config.disable_torque_on_disconnect에따름 | 설치so_follower.py:108,127,160,238 |
| SerialMotorsBus.torque_disabled | 문맥진입시disable,종료finally에서enable | 예외후에도문맥정리로다시ON가능 | 설치motors_bus.py:676~692 |
| Feetech.enable_torque | Torque_Enable=1및Lock쓰기 | 이후명시적해제전까지유지가능 | 설치feetech.py:302~305 |
| bus.connect(handshake=False)/readCalibration | 확인된소스에서는직접ON쓰기를찾지못함 | disconnect(False)는끄지않음 | 기존소스조사및1차READ진단 |

## 이번 무인 관측과 구분

이전 저녁에는 현재위치목표쓰기직후명시적ON없이토크가재활성화되는구간을재현했다. 이번야간의 최초읽기에서는shoulder_pan=1을읽고바로중단했다. 전원을재투입한현재상태에서왜1인지,남은5개토크/모드/온도/위치는UNVERIFIED이다. 이전재현과같은원인이라고단정하지않는다.

무인코드는SDK송신앞에서개별READ외의패킷을거부한다. robot.connect/configure/calibrate와토크/목표/Lock쓰기는실행하지않았다. disable로토크를끄거나전원/서비스를변경해서우회하지않았다.

## 확인된 정규화 근거

LeRobot0.6.0의SerialMotorsBus.read는raw응답의부호를해석한뒤normalize=True일때_normalize를사용한다. 새코드는normalize=False로부호해석된raw를읽고ID사전에_normalize를1회만적용한다. DEGREES/RANGE_0_100은SOFollower생성시use_degrees=True와그리퍼MotorNormMode에서결정된다. 값범위로단위를추측하지않는다. 새경로의실제정규화출력은최초토크ON에서중단돼UNVERIFIED이며가짜버스시험만통과했다.
