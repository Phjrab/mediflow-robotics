# 실행기 심층 읽기 분석 — 2026-11-11

## 결론과 증거의 한계
실기 vla_stream_blue_1794387696614900087에서 wrist_flex 개별63 읽기84/84, bulk34, 종료 개별34/36/36이 관측됐다. 이전 bulk62/direct38과 합치면 bulk 전용 문제라고 설명할 수 없다. 현재 모의검사는 올바른 프로토콜 프레임의 이상 온도값 자체가 진짜 온도인지 검증하지 못한다. 9+10 mock 통과와 camera/model dry 통과는 실기 온도 원인 해결 증거가 아니다. 만졌을 때 뜨겁지 않았다는 추가 현장정보는 실제 전체 모터가84도였다는 설명을 약화하지만 내부센서/전자회로/통신 문제를 독립적으로 확정하지는 않는다.

본 작업은 로컬 파일 읽기와 이 보고서 작성만 했다. 런타임 수정, 테스트 실행, SSH, 포트/카메라/모델 접근 없음. 로컬 SDK는 reports/goal_incident_20261106/source_review 스냅샷이므로 총괄이 확인한 설치 SDK와의 일치가 최종전제다. 이 폴더에는 live steps.jsonl/summary.json 원본이 없어 파형을 직접 재계산하지 않았다.

## 1. 송수신 경계에서 확실한 사실
- SDK port_handler.py:31–32의 clearPort는 self.ser.flush(). RX 입력 비우기가 아니다. 입력 reset_input_buffer는 setupPort:106에서 보인다. 따라서 매 TX 전 clearPort가 이전 수신 바이트를 없앤다는 가정은 틀리다.
- SDK protocol_packet_handler.py:103 rxPacket는 ff ff 탐색, 응답 length를 이용한 프레임 경계, checksum을 검사한다. 이름상 CRC라 부르기 쉽지만 실제는 8비트 합계의 1의보수 checksum이다. 온도값의 물리적 의미/요청주소는 검사할 수 없다.
- 같은 파일:177–209 txRxPacket은 송신 뒤 rxPacket을 반복하면서 요청 ID와 다른 정상 응답은 내부에서 소비한다. StrictIndividualReplies는 이 함수의 최종 반환값을 받기 때문에 중간에 버려진 다른ID 프레임은 strict.history에 남지 않는다. guard가 모든 수신 프레임을 검사/기록한다고 표현하면 부정확하다.
- strict guard(individual_reply_guard.py:28–58)는 반환 프레임에 대해 expected ID, 전체 길이 width+6, length byte width+2, header, checksum, 패킷error 및 SDK error, 통신 결과를 검증한다. 온도1byte 응답은 정확히7바이트여야 한다.
- base direct_live.py:61–77 read_sync_replies는6개 응답을 모두 소비한다. 첫 오류 뒤에도 남은 요청 응답을 소비한 뒤 실패시킨다. 정상 bulk 읽기마다 무조건 뒤에5개 응답이 남는 구조가 아니다. 반환순서는 ids 순서를 요구하며 어긋나면 Stop이다.
- base read_block:245–253은 SDK syncReadTx 후 위 함수를 호출하고 finally에서 is_using=False. syncReadTx는 broadcast라 TX시 RX자동수신을 하지 않으며 6개응답은 이 사용자코드가 소비한다.

## 2. 84/84와 잔여 응답 가설을 구별해야 하는 이유
온도는 bulk 시작56의 offset7=63이며, 개별도 readTxRx(port,id,63,1)로 요청된다. offset 자체는 맞다. helper는 서로 분리된 두 READ를 실행하며 두 번째는 첫값이 bulk와 다를 때 실행된다. 두값은 같은 변수 복사로 만들어지지 않는다.

단순히 이전 bulk15byte 응답이 개별63 응답으로 채택됐다는 설명은 현재 strict 전체길이7 검사와 충돌한다. 같은ID bulk 정상프레임21B가 오면 fail-closed, 다른ID면 SDK내부에서 소비 후 계속 대기한다. mode33/8 응답14B, active target67/2 응답8B, Goal write ack6B, READ request echo8B도 그대로는온도7B 검사를 통과하지 못한다.

통과 가능한 잔여 혼입 후보는 같은ID·1byte·정상checksum의 이전 응답이다. 프로토콜 status frame에는 요청주소나 sequence가 없어 이전63 또는 다른1byte 레지스터의 값을 새63 응답과 구별할 수 없다. 하지만 정상운영중 다른1byte 요청이 어디서 발생했는지, 실제잔여프레임이 존재했는지는 아직 증거가 없다. 정상fullcycle에는 bulk56/15→63/1×6(불일치축 추가1회)→mode33/8이 주로 사용된다. 다른폭응답을 무시한뒤 같은폭잔여가남는 구체적원인 없이는 잔여가설 확정불가다.

두 번84는 독립된 물리온도증명도 아니다. 같은 장치센서/펌웨어상태, 같은 serial/수신파이프라인을 공유하므로 상관오류가 반복될 수 있다. 반대로 두 개의 같은ID/1byte 유효이전응답이 정말 남아 있었다는 증거도 필요하다. 예컨대 ID4/값84/status0인 실제 온도응답이면 원문은 ff ff 04 03 00 54 a4 형태여야 한다. 실제TX/RX 원문이 없으므로 이프레임이 정말 반환됐는지조차 현로그만으로검증불가다.

현재 thermal_read.py는 strict 컨텍스트 객체를 보존하지 않아 strict.history의 raw_hex/expected_id/address/expected_bytes를 실행로그로전달하지 않는다. thermal_source_disagreement는 디코딩된 값만 기록한다. 또한 SDK 내부에서 소비된 프레임/프레임앞 잡음, TX직전 RX잔여길이, 요청과응답 시간은 기록되지 않는다. 따라서84/84 원인 규명에 필요한 핵심관측이 빠져있다.

## 3. 우선순위가 있는 원인 후보와 구별 증거
1. 수신 트랜잭션 경계/동일폭 이전응답: TX전 RX대기량과 전체 TX/RX 프레임순서, ID/길이/checksum/주소요청/단조증가seq를 같이 기록해야 검증된다. 실제수신된 raw가정상이고요청직전잔여0이며응답시각이후에도84이면 단순잔여가설은약해진다. 입력버퍼무조건삭제는 원인을숨기거나지연정상응답을버릴수있으므로 원인증거없이치료로확정하면안된다.
2. 빈도/복합요청에 따른 장치·어댑터·펌웨어응답 이상: 정지상태63단독과fullcycle을각20/30/50/60Hz로비교하면목표쓰기없이읽기율의영향을분리할수있다. 이상횟수/전체프레임수/실측주기/잔여/timeout/다른ID소비수까지비교해야한다. 0회라고유한표본에서부재가증명되지는않는다. 이대조는총괄소유이며이담당은실행안함.
3. 움직임/전원/배선/모터내센서: 정지읽기에는정상이고실제움직임에서만나타나면요청율만으로설명안된다. 전압62/부하60/전류69/토크/실제목표67와같은시점원문이필요하다. 외부온도관측도센서오류와열상승을구분하는보조증거다. 현재자료로특정축기구·배선수리나토크증가를정당화하지않는다.

임계값완화/온도더블필터/평균필터/높은값무시는 제안하지 않는다. 현재55HOLD, 통신오류쓰기차단, 현재위치HOLD예외를 유지한 채 관측부터 개선하는 것이 필요하다. 런타임관측수정여부는총괄결정이다.

## 4. 속도와 시퀀스 시간축
stream_live.py:178–330에서 rate=30*speed=9.9 model actions/wall-second. phase는모델액션index이며 활성버퍼가있는전송중벽시계차이×9.9로누적된다(:228). playback_s=phase/30은모델시간이다. 50/60Hz는패킷/센서주기이고모델액션9.9Hz와다른축이다. speed0.33을줄여도센서/패킷루프자체를20Hz로줄이지않는다. 따라서teleop20Hz와VLA50/60Hz의요청율차이는재생배율만으로해결되지않는다.

각루프에는bulk56/15,개별63/1×6~12,실행중mode33/8,정책Goal broadcast가들어간다. 온도helper추가전보다요청수/왕복수가늘었다. 최소개별6회만해도온도READ는50Hz에서초당300회,60Hz에서360회이며불일치추가읽기는더한다. 실제통신baud/요청별지연측정없이대역폭포화나60Hz가원인이라고확정할수없다. 총괄정지20cycle 최대5.91ms/이전dry59.39Hz는그당시부하에서가능했다는증거일뿐동작중상관오류부재증거는아니다.

최초추론중은sent_phase=None으로phase0이유지된다. 기존버퍼재생중다음묶음을요청하면origin=first_unused, elapsed추론시간×9.9의floor만큼expired를계산한다. usable=max(first_unused,origin+expired),skip=usable-origin을적용해추론중이미지난액션을제외한다(:86,:239). 기존버퍼와겹치는최대10액션은merge로블렌딩하고그사이는smoothstep보간한다. 순서의기본은증가index이며과거index를그대로재생하는코드는아니다. 단버퍼끝에서phase는min(...,buffer_end)로멈추므로실제누적모델시간은전체벽시간×0.33보다작을수있다(초기추론/버퍼정지제외).

중요:make_policy:751은StreamingPolicy(...,rtc=False). stream_policy.py의prev_chunk_left_over/inference_delay/execution_horizon을사용하는RTC안내분기는비활성이다. 지금의expired/skip은실행기측시간정렬이지활성RTC모델guidance가아니다. 모델은매번관측상태로chunk를만들고실행기는그동안경과한앞부분을버린다. 정확한실행후순서는inference origin/seconds/expired/skipped/discarded와goal model_phase를함께봐야한다. seconds는worker에서pack+predict소요,메인루프에서job완료를발견하기까지의추가지연은포함하지않을수있다. first_unused도하한을강제하므로완전히이미지난것은막지만,렌더/추론회수지연과보간연속성은원본trace없이확정평가불가다.

모델타임라인2.565초는50Hz패킷이모델50Hz로진행됐다는뜻이아니며,집기전체동작이진행될충분한모델시간이었는지도원본데모/계획과비교해야한다.

## 5. 팔꿈치 목표7.82도/실제1.14도의 해석
보고된command_span/observed_span=14.6%는시간정렬된추종성능이아니다. 관측은각loop전송전위치,명령은전송직후계획값이며실제마지막반응을기다리지않고온도로중단됐다. 원본trace로최대/평균/끝단 signed error,시계열lag,목표변화방향/유지시간을봐야한다.

base.plan:278–284는target→bounds clip→unnormalize→raw clip→wire_normalized를기록한다. runner관측commands는이wire_normalized에대한범위라면단순모델unclipped목표와실제범위를혼동한수치는아니다. elbow command상한3077은start3083과별개이고이미목표에적용된다. 그러나wire送信검증은패킷바이트일치이지모터실제active target67일치증명이아니다. 실행중checked는mode/torque를읽지만모든목표마다active target67을읽어전송값이수락됐는지기록하지않는다. start_snapshot은67을읽지만정책매step은아니다.

따라서현재후보는(1)서보가목표수락했으나설정속도/가속/전류/부하/기구조건으로못따라감,(2)목표수락/내부목표처리차이,(3)bulk현재위치피드백신뢰성,(4)모델chunk재계획/skip/짧은목표유지시간과중단이다. 특정원인확정불가. 온도bulk와individual모두이상사례가있으므로현재위치bulk가정확하다고자동가정하면안되지만오류라고단정할수도없다. 목표42/현재목표67/현재위치56의동시계열과개별56대조,부하·전류및읽기전용설정자료가필요하다. 원인자료없이속도/토크/보정/제한을변경하면안된다.

## 총괄에게 권하는 다음 판단 순서
- 우선현재계획한63단독/fullcycle의20/30/50/60Hz읽기대조를관측증거와함께평가한다.
- 84/84를생성한실제개별프레임과전후모든RX경계를보존하도록진단할지결정한다. 기존값필터추가는원인진단대체가아니다.
- 기존live trace에서팔꿈치wire_raw/feedback raw/모델phase/추론skip를시간정렬하고,active target수락증거부재를명시한다.
- 코드변경은이보고서만. 추가runtime수정/실기재실행은이담당이진행하지않는다.

## 추가 실기 증거 반영 — 무동작 개별63 재현
총괄 전달 증거(이 담당이 원격 파일을 직접 읽은 것은 아님): ~/thermal_rate_1794388240889594581.json. isolated_temp63 20Hz 3초 최대45, 30Hz 단계2.4초에 gripper56으로HOLD. goal_writes=0, portclosed, queued_before_read=0.

- 실제 TX: FF FF 06 04 02 3F 01 B3 (ID6, READ63, width1).
- 실제 RX: FF FF 06 03 00 38 BE (status0, 값0x38=56, 정상checksum).
- 약0.5ms 뒤 동일TX에 RX FF FF 06 03 00 26 D0 (값0x26=38) 세번.

이 증거는 잘못된 코드정수변환/주소offset만으로 이상값이 생겼다는 설명을 배제한다. 실제wire 응답 payload가56을포함했다. 움직임/Goal전송/bulk읽기/60Hz가없어도재현되므로 이요소들이이상온도발생의필수조건이라는가설도배제된다. queued_before_read=0은관측순간이미큐에쌓인응답가설을약화하지만장치/USB/어댑터내부지연이나그후도착프레임까지증명하지는않는다.

이전보고의동일폭잔여가설은현재우선순위를낮춰야한다. 현재관측은ID6/1byte/status0/checksumvalid라는전체프레임과요청을확보했으며,bulk프레임오해석설명은더욱부적합하다. 한편checksumvalid는payload의물리적정확성을보장하지않는다. 센서/모터firmware/전원·전기통신/어댑터중어디서값이만들어졌는지는소프트웨어관측만으로미확정이다. 56→38의0.5ms변화는실제모터전체의열상승·냉각으로설명할수없으나센서/디지털응답오류구분은남는다.

20Hz3초정상과30Hz이상은요청율영향후보지만빈도원인확정은아니다. 표본시간/순서/장치상태가다르고20Hz도향후항상정상이라는보장이없다. 속도를20Hz로낮추거나온도를다중필터링하는것을원인수정으로판정할수없다.

다음소프트웨어작업은원문TX/RX/시각/요청연번/큐상태와오류이유를보존하는진단개선및mock/dry 검증범위로한정하는것이타당하다. 임계상향/높은값폐기/추가live권유의근거로사용하지않는다. 온도55HOLD와통신검증/쓰기차단을유지한다. 런타임은총괄결정대기이며이추가작업에서도수정하지않았다.
