# 토크 ON/OFF 온도 원문 독립 비교

## 독립 pySerial 원문 추가 검증

총괄이 제공한 `thermal_raw_1794393161821456413.json`과 `servo_inventory_1794392766557596574.json`을 읽기만 하여 검증했다. SHA256은각각 `fa92071f50ef1e464f8090be512e34a8a39fd428e2f423b66d38fe5378858b04`, `195d2a4228b954f6e251c817a5b9a3c3cda962e467d8260ecf38c3c1b35ae4ce`다. 실행코드 `raw_temperature_probe.py`는직접serial을사용하고LeRobot/서보SDK import가없는것을읽기로확인했다. 이담당은도구를실행하거나장치에접근하지않았다.

전체5400events=주소63온도5388+주소40토크12다. sequence0..5399가연속이며전부TX8byte/주소40또는63/READ/폭1/header/ID/합검사정상,RX7byte/header/동일ID/length3/status0/합검사정상,decoded value와RXdata일치,모든queued_before_read=0,error필드없음을확인했다. monotonic시각은전체순증가,각row에서unix≤tx_unix≤rx_unix다. phase30Hz300cycle+60Hz598cycle=898cycle,각ID898온도응답으로cycle/개수/ID1..6순서가일치하고기록상누락은없다. 다만기록에없는물리신호/과거소실packet까지없었다는증명은아니다.

각phase는요청10초,모두COMPLETE,최대53°C/최소31°C다. 최종status COMPLETE,port_closed=true,register/goal/torque/configuration writes모두0. 시작/종료6축Torque_Enable응답각6건이모두0이며요약torque_before/after와일치한다. 기록된전체TX는READ뿐이다.

| 축/ID | 원문min–max°C | 동일ID주소63연속관측의최대절대점프 | 해당점프시간(요청시작monotonic차이) |
|---|---|---|---|
| shoulder_pan/1 | 33–34 | 33→34 (+1) | 16.743ms |
| shoulder_lift/2 | 34–35 | 34→35 (+1) | 33.382ms |
| elbow_flex/3 | 31–34 | 32→34 (+2) | 16.583ms |
| wrist_flex/4 | 32–37 | 34→37 (+3) | 16.740ms |
| wrist_roll/5 | 32–43 | 33→43 (+10) | 16.949ms |
| gripper/6 | 35–53 | 36→53 (+17) | 33.523ms |

최대점프는30Hzphase,sequence1763→1769다. TX양쪽 `ff ff 06 04 02 3f 01 b3`,RX는36=`ff ff 06 03 00 24 d2`,53=`ff ff 06 03 00 35 c1`이며각checksum210/193으로합검사255를만족한다. 33.523431ms는monotonic요청시작간격,응답완료unix간격은33.577480ms다. 다음sequence1775는36=`ff ff 06 03 00 24 d2`로33.357218ms(요청시작간격)후복귀했다. 주소40토크0은모든온도점프계산에서제외했다.

이원문은**LeRobot/서보SDK가스파이크발생의필수조건은아님**을추가로뒷받침한다. SDK경로의모든문제가없다는뜻이나펌웨어/배선/전원/USB어댑터/호스트driver/serial경로중특정원인이확정됐다는뜻은아니다. pySerial과OS/USB/공통물리경로는그대로남는다. 55에도달하지않아중단되지않았지만53및43스파이크잔존으로해결선언불가다.

inventory6축모두FW major3/minor10,모델777,Return_Delay_Time0,Max_Temperature_Limit70,Torque_Enable0,Status0을확인했다. Present_Voltage raw는shoulder_lift121,나머지122이며총괄해석으로12.1/12.2V다(원문단위정의자체는inventory에없음). 온도31~53스파이크와70하드웨어보호설정은별개이며소프트웨어55조건을70으로변경할근거가아니다. inventory는decoded스냅샷이며해당각register의원시packet은이파일에없어원문checksum검증범위에서제외한다.

다음DC차단후공통3핀연결재확인제안은총괄의현장후속범위로기록하며이담당은전원/배선/서비스/토크를조작하지않았다. LeLab복구상태도이두JSON만으로독립확인하지않았다.

2026-11-11 KST. 제공된 로컬 JSON/JSONL 읽기와 메모리 집계만 수행했다. runtime/SDK 변경, SSH, 장치 접근, 추가 실험은 없다. ON 표시는 총괄이 전달한 실험 조건이며 ON 로그 안에 토크 readback은 없다.

## 결론

기록상 한 번의6축Torque_Enable=0패킷 후 OFF readback이 확인되고, OFF 상태를 시작 시 재확인한 추가20초 시험에서도 형식정상 온도 스파이크가 남는다. 따라서 **활성 토크/PWM만이 오독의 필수 원인이라는 설명은 지지되지 않는다.** 토크 OFF로 공급전원·통신·센서·모든 내부 전기활동이 없어지는 것은 아니므로 전기적 원인 전체 배제나 특정 펌웨어 원인 확정은 불가하다.

OFF에서55°C중단은 없지만37→54→37°C,33→45→33°C 등 빠른 스파이크가 관측된다. 임계 미도달은 해결 증거가 아니다. ON/OFF는 연속된 시각·동일 표본수·동일 모든phase를 갖춘 통제 비교가 아니므로 발생률 개선이나 인과 효과를 확정할 수 없다.

## 원문 지문

| 파일 | SHA256 |
|---|---|
| thermal_rate_1794388240889594581.json (이전 ON 전달 조건) | fc3423c8176281116dc6987a8712c903d3b12b88550bf7fb8e2b7099ddc9f72c |
| thermal_rate_1794392305497959619.json (OFF sweep) | 46a9b7e39ed6603b24f1740e4addd1cd76edad523eb56a3425700dd4851fbf70 |
| thermal_off_long_1794392395340705132.json | 6b5b2e4a9892ccb6df68dc6a2961c54af470a4c640f46a41870425e331b648bf |
| torque_off_events.jsonl | 13abe199bfa2b0634b2323f5b31d4bbfc7201c72039e8eb6ab2a9ada9bd4d2cb |

## OFF 전송과 재활성화 여부의 증거 범위

torque_off_events.jsonl에는 PORT_OPEN,6축ON readback,AUTHORIZED_WRITE1건,WRITE_TRANSMITTED1건,6축OFF readback,약253ms후6축OFF 재확인,RESULT authorized_writes=1,PORT_CLOSE가 순서대로 있다. 실제 허가 패킷은 `fffffe1083280101000200030004000500060030`이며20byte,ID254,instruction131,sync주소40,폭1,ID1..6에값0,checksum합255를 만족한다. Goal_Position주소42 패킷이 아니다.

OFF sweep은goal_writes=0,error=null,port_closed=true이고 trace5760건은 모두주소63/폭1 READ다. 다만 sweep trace 자체에는 주소40 토크 재독이 없어, sweep 전구간의6축OFF를 이 파일 하나만으로 직접 관측한 것은 아니다.

OFF long은 시작 trace6건이주소40/폭1 READ이며ID1..6의응답이모두0이다. 이후7110건은주소63/폭1 READ다. goal_writes=0,error=null,port_closed=true. 토크OFF사전확인 후20초대조라는 조건을 뒷받침한다. 종료시토크 readback이나 연속토크계측은 없다.

제공된 이벤트·계측 범위에서 재활성화 전송은 보이지 않는다. 하지만 goal_writes=0은 토크ON전송 카운터가 아니며, readTxRx trace만으로 별도sync-write 전송/외부프로세스의부재까지 증명할 수 없다. 그러므로 정확한 표현은 **“허가된OFF1패킷·직후/settle OFF·long시작OFF·추가goal0·포트닫힘 확인, 제공된기록에재활성화없음”**이다. 포트닫힘은두결과필드와OFF이벤트의근거이며독립OS조회는하지않았다.

## phase별 최대온도와55중단 비교

최대는6축전체에서 구했다. bulk와individual은 서로 다른 관측으로 구분한다.

| 조건 | phase | cycle수 | individual최대°C | bulk최대°C | 55중단 |
|---|---|---:|---:|---:|---|
| 이전ON | individual20Hz | 60 | 45 | 없음 | 이phase없음 |
| 이전ON | individual30Hz | 73 | 56 | 없음 | 있음,후속phase중단 |
| OFF sweep | individual20Hz | 60 | 50 | 없음 | 없음 |
| OFF sweep | individual30Hz | 90 | 38 | 없음 | 없음 |
| OFF sweep | individual50Hz | 150 | 54 | 없음 | 없음 |
| OFF sweep | individual60Hz | 180 | 52 | 없음 | 없음 |
| OFF sweep | full20Hz | 60 | 37 | 43 | 없음 |
| OFF sweep | full30Hz | 90 | 40 | 53 | 없음 |
| OFF sweep | full50Hz | 150 | 37 | 54 | 없음 |
| OFF sweep | full60Hz | 180 | 48 | 47 | 없음 |
| OFF long | individual60Hz | 1185 | 52 | 없음 | 없음 |

OFF sweep은총960cycle/5760개별reply,OFF long은1185cycle/7110온도reply+시작6torque reply=7116trace다. 이전ON은133cycle/801reply이며56검출후3재독을포함한다. ON50/60/full은비교원문에서미실시라동일조건대비가불가능하다. OFF20Hz최대50은이전ON20Hz최대45보다높아단순히“OFF로모두개선”됐다는설명에도맞지않는다.

実測sample間隔平均はOFF sweep20/30/50/60Hzそれぞれ約50.10/33.42/20.08/16.75ms、OFF long16.879ms(約59.24cycle/s)。longは最初〜最後sampleの時刻差19.985秒で、要求60Hz20秒に近いがちょうど1200cycleという記録ではない。

## 軸別最大と最大スパイクの原文

| 軸/ID | OFF sweep min–max°C | OFF long min–max°C |
|---|---|---|
| shoulder_pan/1 | 33–34 | 33–34 |
| shoulder_lift/2 | 34–36 | 33–35 |
| elbow_flex/3 | 32–35 | 32–34 |
| wrist_flex/4 | 33–39 | 33–38 |
| wrist_roll/5 | 32–44 | 32–45 |
| gripper/6 | 36–54 | 35–52 |

sweep최대는raw index1493(0부터),individual50Hz,시각16869.415801886,ID6다. TX `ff ff 06 04 02 3f 01 b3`,RX `ff ff 06 03 00 36 c0`의data54/checksum192는합검사정상이다. 같은ID직전37에서20.086ms후54,20.181ms후37로돌아온다.

long최대는index1337,시각16959.063751888,ID6,RX `ff ff 06 03 00 34 c2`(52/checksum194)다. 16.939ms전37,16.599ms후37이다. 손목회전최대는index2728,시각16962.950102364,ID5,RX `ff ff 05 03 00 2d ca`(45/checksum202)다. 16.829ms전33,16.647ms후33이다. 이상표시는gripper에만한정되지않는다. 이집계는주소63만필터했으며시작주소40토크0값을직전온도로사용하지않았다.

전체OFF trace12876건에서TX길이8/header/READ/폭1/합검사,RX길이7/header/동일ID/status0/합검사,comm/status0확인결과불합격0이었다. 주소63은12870건,주소40은6건이며queued는전부0이다. 이는기록된SDK경계에서형식이맞다는뜻으로센서진실성·물리wire독립계측·다른ID내부소비검증은아니다. full-cycle의bulk최대는sample내decoded값이며trace에bulk TX/RX전체가없어그원문checksum을독립검증했다고말할수없다.

## 解釈と残る確認

温度を示す応答はOFFでもchecksum正常なまま急変する。単純checksum失敗を捨てるだけでは検出できず、55未満の異常は既存gateを作動させない。独自フィルタや安全条件変更は行っていない。

모터형상/설치/온도/배선등의동일성은JSON만으로완전확인할수없다. 총괄추가정정에따르면사용자는처음부터본체직결이었고USB외장허브를원인으로단정하거나경로이동을요구할근거가없다. 본체내부허브표시와외장허브사용을구별해야한다. 이문서는USB물리경로를독립검증하지않았다. OFF에서도스파이크가있다는사실은토크ON/PWM을필수조건으로하는단독가설을약화하지만,토크ON이빈도/크기에기여하지않는다는완전배제는아니다.
