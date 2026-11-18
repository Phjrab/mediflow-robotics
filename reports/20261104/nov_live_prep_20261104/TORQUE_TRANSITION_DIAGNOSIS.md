# 토크 OFF 확인 후 다음 실행에서 ON이 되는 현상

## 현재까지 확정한 것

운영자 실행에서 --disable은6개토크0을읽고TORQUE_OFF_SUPPORTED로종료했다. 뒤의 --enable 시작진단은6개모두1을읽어쓰기전거부했다. 이현상이반복됐다. 동일OFF/ON안내를반복한것은원인해결이아니었으므로중단했다.

운영자가제공한설치소스에서 SOFollower.__init__/is_calibrated, Feetech.is_calibrated/read_calibration, SerialMotorsBus.connect/_connect/disconnect(False)/set_timeout, PortHandler.openPort/closePort/setupPort/setBaudRate에는토크ON을직접쓰는경로가확인되지않았다. 사용자서비스목록에서LeLab/YOLO/카메라미리보기는inactive, Astra카메라브리지만active였다. 이목록만으로모든다른프로세스/하드웨어동작을배제할수없다. SmolVLA인터프리터가LeLab쪽패키지소스를쓰는사실자체를원인으로단정하지않았다.

**원인UNVERIFIED.** 새로운토크허용/자동OFF→ON/가드우회/라이브실행으로진행하지않는다. 기존home/보정/온도/수치/속도/토크게이트는그대로유지한다.

## 새 추적 도구

torque_transition_probe.py는운영자SUPPORTED확인후같은SDK/같은follower포트에서다음경계를한프로세스로기록한다.

1. 버스최초연결직후토크·온도·위치·모드.
2. 읽기보정검사직후.
3. 명시적Torque_Enable=0한번직후와같은연결에서잠시관측.
4. 토크해제없이포트를닫고다시열었을때,보정검사전/후관측.

현재보정해시/home형식및5도범위/위치모드0/그리퍼범위/온도50미만을검사한다. home/상태가다르면진단을중단한다. 카메라/정책/모델/Goal_Position/토크ON명령은사용하지않는다. 팔/물체를운영자가지지해야하며,외부원인으로ON이재발할가능성은이검사만으로막지못한다.

SDK PortHandler.writePort 앞에 이프로세스에만유효한가드를설치한다. 허용패킷은ID1~6의개별READ,그리고확인된OFF단계에서Torque레지스터40에0을각ID당한번쓰는패킷뿐이다. 기존release와같은6개broadcast sync OFF를사용한다. 그외ON/목표/연속영역쓰기/지연쓰기/action/reset/알수없는프레임은전송전에차단하고파일명·줄·함수스택을남긴다. SDK상수와Torque레지스터(40,1)를현재설치본에서검증하기전에는포트를열지않는다. 라이브러리파일은수정하지않으며가드는프로세스안의일시적인Python메서드랩이다. 다른프로세스/직접USB제어/펌웨어동작을전역적으로막는방화벽은아니다.

패킷규칙근거: 제조사 [protocol_packet_handler.py](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/protocol_packet_handler.py)와 [scservo_def.py](https://github.com/ftservo/FTServo_Python/blob/main/scservo_sdk/scservo_def.py). 확인한0.6.0테이블은Torque_Enable=(40,1)이다. 런타임상수나형식이다르면추정해서계속하지않고거부한다.

종료는disconnect(False)를요청한다. SDK종료에예외가나더라도가드복원전원시serial핸들을닫아열린핸들을남기지않도록한다. 강제종료/전원상실/반환하지않는드라이버에서즉시정리는보증하지않는다. 로그는새로운별도출력폴더events.jsonl에저장하며기존파일을덮어쓰지않는다.

## 결과를 해석하는 방법

|결과|확인된경계|아직판단할수없는것|
|---|---|---|
|BLOCKED_WRITE|이프로세스에서금지된패킷송신시도.스택에후보함수기록|호출실패원인과실제목적은소스대조후확정|
|OFF_WRITE_NOT_CONFIRMED|OFF직후에도0확인실패|쓰기실패/응답/외부간섭을더구분해야함|
|REENABLED_WHILE_OPEN|OFF후같은연결에서1재발|외부프로세스/펌웨어등의정확한주체|
|REENABLED_ACROSS_CLOSE_REOPEN|닫기와재연결사이에1재발,재보정검사전|제어선/전원/외부프로세스/펌웨어의정확한원인|
|REENABLED_DURING_CALIBRATION|재연결후읽기보정검사구간에서값변화|패킷차단로그와함께판단해야하며이결과만으로읽기가토크를켰다고단정불가|
|NOT_REPRODUCED_IN_ONE_PROCESS|이한프로세스의경계에서는재현안됨|프로세스종료/새프로세스초기화/일시적외부간섭은남음|

새도구결과를받은뒤해당경로만수정한다. 원인미확인상태에서기존게이트를완화하거나LeLab/SDK를덮어쓰지않는다.

## 검사·실행 범위

- `python -B -m unittest discover -s reports/nov_live_prep_20261104 -p 'test_torque_transition_probe.py'`:17통과,실패0,오류0,제외0,exit0. 가짜포트/버스/상태전환으로읽기허용·OFF한번허용·ON/목표/리셋차단·송신전차단·재연결분류·보정중ON시도차단·home실패무쓰기·종료예외핸들닫기를검사했다.
- Python3.10문법검사통과. 인자없는PLAN_ONLY실행통과(장치/SDK import없음).
- 실제Jetson/serial/모터/카메라/전원/추론/학습은NOT_RUN. 파일전송/설치/push도하지않았다. 이번새결과는사용자지시에따라GitHub업로드보류다.

운영자실행순서: 먼저새진단파일만전송한다. 그다음LeLab/모니터종료·포트비점유와팔지지를확인한운영자가별도실행한다. 이단계가원인확정이나수정완료를뜻하지않는다.
