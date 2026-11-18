# home 웹 모니터 / 일부ON 토크 해제 검토

## 후속 정정 — 붙여넣기 명령이 SSH 셸을 종료한 문제

사용자 출력에서 이미 존재하는 검토본을 감지한 exit 1이 대화형 SSH 셸 자체를 종료했다. 최초 붙여넣기 생성 명령에 set/exit를 직접 둔 안내 문제다. PASTE_HOME_ALIGN_UPDATE.sh, PASTE_TOOLS.sh, PASTE_UNITS.sh 전체를 하위 셸 괄호로 감싸 set 옵션과 exit가 그 안에만 적용되도록 수정했다. Bash -n 세 파일 통과. 기존 Jetson 파일의 내용/완성도는 존재 여부만으로 확정할 수 없으므로 파일 해시/문법을 먼저 읽어 확인한다. 기존 파일 삭제/덮어쓰기나 Jetson 재접속을 대신 실행하지 않았다. 원본 모니터/장치/웹의 실제 실행은 이번 로컬 수정에서 NOT_RUN, push도 하지 않았다.

이번 요청 범위는 코드·문서·정적 검사·모의 검사다. Jetson 접속, 장치 열기, 웹 서버 실제 실행, 추론, 학습, 파일 전송, push를 하지 않았다. GitHub 지속 기록 요청은 유지하지만 이번의 명시적 push 금지를 우선해 이 결과는 로컬에만 기록한다(NOT_PUSHED).

## 읽은 원본과 API

- 사용자 파일: 다운로드 폴더의 home_align_web.py. 원본은 수정하지 않았다. SHA256=9cc4e24e06b9918588333fafddebfa3a5be2e389718e69ac487b31e1f26179fc.
- 보유 공식 lerobot0.6.0 휠의 motors/motors_bus.py850~877행: _normalize는 int motor id→raw 값 딕셔너리를 받아 같은id→정규화값을 반환한다.
- 같은 파일991~1023행: read(normalize=False)도 부호해석은 수행하며, normalize=True인 Present_Position은 내부에서 _normalize를 호출한다.
- 따라서 원본36행의 bus._normalize({m.id: raw})[m.id]는 확인한소스와맞는다. 관절이름을키로넣으면맞지않는다. 수정본은공개 read(normalize=True)를쓰므로비공개함수에직접의존하거나중복정규화하지않는다.
- 모터SDK/LeRobot의Jetson설치본소스가이번보유휠과완전히동일한지는미확인이다.

## 원본에서 발견한 문제와 수정

|항목|원본|수정본|
|포트|13행 기본 /dev/ttyACM1. 실제follower인지보장없음|사용자물리확인기록의follower stable by-id로고정|
|모터쓰기|connect(handshake=False), read, _normalize, disconnect(False)뿐이라원본에도직접모터쓰기경로없음|같은원칙유지.3개레지스터만read, 토크/Goal/configure/calibrate/send_action 호출없음|
|초기화실패정리|버스연결24행·스레드시작82행·HTTP생성83행이try85행밖. HTTP포트중복등으로시작실패시정리누락가능|HTTP bind를버스열기전에완료. 버스열기부터전부try/finally로정리|
|스레드/닫기경합|reader가막혀도종료시0.4초만대기하고버스를닫음|모터read와close를메인스레드한개가순서대로수행. HTTP스레드는잠금보호캐시만조회|
|신선도|브라우저/서버연결오류때기존초록표시가남을수있음|1.5초관측지연/읽기오류/통신실패면기존행과통과표시무효|
|표시정확성|rounded diff로색상을판정. 그리퍼도각도처럼보일수있음|반올림전차이로3/5판정,5축degree/그리퍼퍼센트포인트구분|
|토크ON안내|전원재투입이면모두OFF라고단정|전원상태는조회로확인. ON이면수동이동중단,모니터종료후지지·SUPPORTED해제안내|
|수치근거|home 이름/순서/값검사부족|6축이름/순서/유한값/그리퍼범위/5도허용폭/보정해시검사. home평균으로자동이동하지않음|

정상 종료/Ctrl+C/일반 예외/HTTP스레드시작실패/독점설정실패 경로의 포트 정리를 모의 검사했다. SIGKILL/전원상실/드라이버가반환하지않는상황에서즉시정리를보증하지않는다. HOME_MONITOR_CLOSED가없거나점유PID가남으면다음단계중단.

## 토크 도구 변경

- --enable의각사전점검에서MOTOR_DIAGNOSTICS로관절별Torque_Enable/현재위치/home목표/차이/단위/위치모드/온도를출력한다. 거부예외에는ON관절이름을적는다.
- --disable은원래도전체OFF조건이없었다. 일부ON/전체ON상태에서6개Torque_Enable에0을쓰고다시읽는다. Goal_Position은쓰지않는다. SUPPORTED확인과--confirm-supported/--execute가필수다.
- 토크해제를위한home파일은필수가아니다. home이없거나유효하지않으면차이를UNVERIFIED로출력한다. enable은유효한home과기존게이트를계속요구한다.
- 보정/통신오류는자동우회하지않는다. 해제후에도ON이면관절이름을출력하고지지를유지하도록한다.

## 어떤 도구가 토크를 켜거나 남기는가

|도구/경로|토크를켜는가|종료후상태|
|torque_so101 --enable --execute|Goal=현재위치검사후Torque_Enable=1을직접씀|disconnect(False)이므로ON유지. enable전송후예외/중단이면일부ON가능|
|torque_so101 --disable --confirm-supported --execute|끄는전용경로.6개Torque_Enable=0|읽어확인한OFF를유지.부분실패가능성은상태출력으로구분|
|v4 --live|이미ON을요구하며스스로토크를켜지는않음. 목표/HOLD전송은있음|종료시토크를끄지않으므로기존ON유지|
|v4 --dry-run|토크/목표쓰기없음|시작때OFF면OFF,ON이면ON을그대로유지|
|첨부원본/검토본home모니터|토크/목표쓰기없음|토크변경없이포트만닫음. OFF→ON원인으로단정하지않음|
|일반SO101Follower.connect/LeLabteleop·record계열|확인한표준connect는configure의토크맥락에서토크를다시켤수있음|실제LeLab버전/설정/종료경로에따름.이번원인으로확정못함|
|follower전원재투입|소프트웨어도구가아닌물리절차|시작시자동서비스/펌웨어상태를추정하지않고실제6개레지스터로OFF확인|

disable_torque_on_disconnect=False는OFF→ON을만드는명령이아니다. 연결종료때현재ON을자동해제하지않는설정이다. 이번거부에서어느관절이ON이었는지,어떤프로세스가마지막으로켰는지는추가실행출력전까지UNVERIFIED다.

## preflight 검토

기존판정도fuser종료0(점유발견)이면거부한다. 후속판정함수require_no_owners에추출하고모니터PID가있다는가짜결과/권한오류/비점유세경우를같은Bash함수에주입해검사했다. 실제 fuser/systemctl/장치는실행하지않았다. 모니터가follower포트를연상태이면⑤를막는설계다. 기존점유검사와실제open사이의경합을완전히없앤것은아니며다른앱을동시에실행하지않아야한다.

## 파일 준비와 운영자 실행 순서

PASTE_HOME_ALIGN_UPDATE.sh 전체내용을Jetson터미널에붙여넣으면새이름4개만생성한다. 기존원본/기존Jetson도구를덮어쓰지않는다. 파일이이미있으면중단한다. 생성자체는서버/serial을실행하지않는다.

1. 사용자가선택한전원재투입또는지지확인--disable로토크OFF준비. 실제OFF인지는모니터/도구가읽어확인한다.
2. bash ~/run_home_align_reviewed.sh. 같은LAN브라우저에서 http://<JETSON>:8765를열고모든토크OFF/온도50미만/신선도를확인한후손으로맞춘다.
3. Ctrl+C → HOME_MONITOR_CLOSED → bash ~/nov_preflight_reviewed.sh에서비점유통과.
4. 그다음에만검토본torque도구로⑤진행. 자세가움직였으면새로home/온도/토크게이트가확인하며,모니터와동시에실행하지않는다.

상세절차와통과/중단기준은RUN_ORDER.md의④-1및⑤에반영했다.

## 검사 결과

- python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_*.py':34통과,실패0,오류0,제외0,exit0. home웹관련14개+준비도구20개.가짜bus/server/cache를사용했으며HTTP소켓도열지않음.
- Python3.10문법검사통과. Bash -n:후속heredoc/초기heredoc/preflight/home실행래퍼통과. 실제실행미확인.
- 다운로드원본변경0. 기존v4변경0. GitHub반영NOT_PUSHED(이번사용자금지).

실제브라우저접속/네트워크/8765점유/센서읽기/포트해제/토크해제의현장결과는모두NOT_RUN/UNVERIFIED다. 모의검사를실기성공/G5/G6로해석하지않는다.
