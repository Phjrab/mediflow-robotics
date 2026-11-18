# PyBrain EP.10 영상·RoboSEasy 문서·우리 v3 실행기 비교

2026-11-11 KST. 요청 영상은 [PyBrain EP.10](https://www.youtube.com/watch?v=ElZvzKRt9g8&t=1590)이다. 최초 배정의 “영상 작성자 RoboSEasy”는 총괄의 실제 브라우저 확인으로 정정됐다. **PyBrain 영상과 RoboSEasy 문서는 다른 자료다.** 이 담당은 총괄이 내보낸 영상 자막 원문을 직접 읽었고, 영상 화면 자체의 CLI 옵션은 총괄이 별도로 확인 중이다. 아래 영상 사실은 자막에 있는 범위이며 화면을 본 것처럼 서술하지 않는다.

## 핵심 답변

로봇 설정→보정→리더 시연 녹화→정책 학습이라는 큰 과정은 같은 계열이다. 그러나 **현재 우리는 영상과 똑같은 모델·실행 방법으로 운용하고 있지 않다.** 영상은 ACT·2카메라·`lerobot-record` 정책 평가이며, 우리는 SmolVLA·3카메라·별도 custom streaming runner다. 특히0.33재생배율/50~60Hz목표전송/비동기chunk스킵·겹침·보간/그리퍼완화가 추가되어 “영상대로 그대로 실행 중”이라고 답하면 부정확하다.

이 차이가 곧 현재 오류의 원인이라는 결론은 아니다. 온도 스파이크는SDK없는READ-only·토크OFF에서도재현됐으므로, 모델/학습/eval방식 차이만으로 설명할 수 없다. 위치 추종/작업 성공은 별도로 모델 입력·출력·실제 목표·피드백의 시간축을 점검해야 한다.

## 1. 영상 자막에서 직접 확인한 사실

자막 원문은 총괄 제공 로컬파일 `youtube-ElZvzKRt9g8-f4f52247-d91a-421d-ad69-bd4334bbd1c3.txt`이며 경로는 이 문서 끝에 기록했다. 언어표시는en-US, authored or unspecified다. 번역/전사 오류 가능성이 있어 정확한 옵션은 화면 확인과 대조해야 한다.

| 항목 | 영상에서 확인한 내용 | 시각 / 자막파일행 |
|---|---|---|
| 사용자 링크26:30 | 녹화시작음과동시에리더시연을시작하는구간. 정책추론설명구간이아님 | 26:30–26:46 / 462행부터 |
| 기본환경 | HF LeRobot설치안내,Python3.10 conda,Feetech옵션설치 | 00:39–05:12 |
| 보정ID | my_follower/my_leader와보정JSON식별자가맞아야함 | 13:29–13:59 |
| 카메라 | wrist위카메라를front로이름붙이고index4,top은index0. 640×480/30fps | 17:03–18:28 / 306–330행,23:50–23:59 / 418–420행 |
| 녹화 | lerobot-record,so101_follower/so101_leader,자연어task,10episode씩수집,화살표로다음/환경재설정 | 19:42–28:41 |
| 학습 | 100000steps완료. 발표자는자기loss가일찍수렴했다고설명 | 30:25–31:24 / 507행부터 |
| 정책평가 | lerobot-record,eval_데이터셋이름,5episode,50초episode/20초reset,동일카메라·동일task | 31:36–32:52 / 524행부터 |
| 체크포인트 | outputs/train/act_clean_desk/checkpoints/last,20k간격저장,last는100k | 32:55–33:30 / 548행부터 |
| 모델종류·시연수 | ACT라고직접말하며100episode로실행결과설명 | 33:53–34:19 / 564행부터 |

영상의실제LeRobot commit/version,로봇`use_degrees`,학습normalization통계,정책chunk/temporalensemble수치,평가loop의실측Hz,안전온도gate는자막에서확인하지못했다. 영상카메라30fps를서보목표전송30Hz로단정할수없다. 발표자의수렴/성공경험을우리SmolVLA의필요학습량이나성공률보증으로전용하면안된다.

## 2. 별도 자료: RoboSEasy 공식 문서·공식 GitHub

[RoboSEasy Record & Replay](https://roboseasy.github.io/docs/physical-ai/lerobot/so-arm/software/record-replay/)는리더시연`lerobot-record`와기록episode의`lerobot-replay`를구분한다. 예시top/wrist는640×480/25fps,문서의dataset.fps기본설명은30Hz다. 카메라fps와dataset/제어주기는구분해야한다. 이수치는PyBrain영상30fps예시와동일하다고쓰면안된다.

[RoboSEasy ACT](https://roboseasy.github.io/docs/physical-ai/lerobot/so-arm/policy/act/)는`policy.type=act`학습과`lerobot-record --policy.path`평가를안내하고,별도async_inference서버/클라이언트예시도제공한다. “튜토리얼계열은동기eval만사용한다”는일반화는틀리다. 우리custom코드가그공식async예시와동일하다는증거도없다.

[RoboSEasy SmolVLA](https://roboseasy.github.io/docs/physical-ai/lerobot/so-arm/policy/smolvla/)는`roboseasy/smolvla_base`파인튜닝,top/wrist→camera1/2 rename,자연어task,별도정책평가예시를보여준다. ACT와정책종류가다르다. 페이지에일부“ACT”표제와카메라이름예시가섞여있어한줄복사보다저장checkpoint입력계약을기준으로해석해야한다.

작성자공식GitHub는[roboseasy/roboseasy.github.io](https://github.com/roboseasy/roboseasy.github.io)이며이는RoboSEasy문서운영주체근거이고PyBrain영상의저장소라는뜻이아니다. 또한[roboseasy/lerobot-inference-vla](https://github.com/roboseasy/lerobot-inference-vla)는저장없이추론하는자체스크립트를제공한다. [실제파일](https://github.com/roboseasy/lerobot-inference-vla/blob/main/lerobot_inference.py)의InferenceConfig기본fps25,저장pre/postprocessor로드,관측→predict_action→robot.send_action→주기잔여sleep 경로를확인했다. 작성자도기본record외별도추론도구를가지므로“custom이면무조건틀림”은아니다. 우리stream의skip/blend/별도wire보간과같다고해석하면안된다.

버전주의: 현재[HF 공식 cheat sheet](https://github.com/huggingface/lerobot/blob/main/docs/source/cheat-sheet.mdx)는v0.5.x이하와새rollout명령을구분한다. 현재main을옛영상당시동작의근거로소급하지않는다. 현재[record 소스](https://github.com/huggingface/lerobot/blob/main/src/lerobot/scripts/lerobot_record.py)도policy배포와record분리가변했으므로영상명령이현재설치버전에서동일하게동작한다고가정하지않았다.

## 3. 우리 로컬 코드와의 차이표

아래줄번호는이번읽기시점파일기준이다. metadata/과거배포후보는설치실체와구별한다. LeLab0.6.0·new3cam·137episode·20k현재선택은총괄현황과로컬저장metadata를함께참조하며실제Jetson재조회는하지않았다.

| 비교항목 | PyBrain영상/별도문서 | 우리v3 및 로컬 근거 | 의미 |
|---|---|---|---|
| 정책 | 영상ACT | metadata config.json:2 smolvla,process_policy.py:80 SmolVLAPolicy | 동일알고리즘아님 |
| 학습량 | 영상100episode/100ksteps | train_config.json:3 repo명137,198 steps20000 | 데이터/훈련조건다름. 많고적음만으로원인확정불가 |
| 카메라 | 영상front/top2개640×480/30fps | policy_api.py:17 end_effector/ceiling_vertical/ceiling_oblique3개;stream_live.py:616 카메라640×480/30fps | 키·시야·장치가다름. 이름복제보다학습시야일치필요 |
| 모델camera키 | 영상세부rename미확인;RoboSEasySmolVLA는2뷰예시 | policy_preprocessor.json:8–10 end_effector→camera1,vertical→camera2,oblique→camera3 | rename한번만적용해야함 |
| 상태단위 | 영상미확인 | direct_live.py:193 use_degrees=True,255 bus._normalize;몸degree/그리퍼0–100별도계약 | 영상과수치범위가같다고단정불가 |
| 모델정규화 | 영상/ACT정확한통계미확인 | metadata config.json:56 VISUAL IDENTITY,STATE/ACTION MEAN_STD;process_policy.py:88 저장pre/post로드 | 로봇단위변환과모델통계정규화는다른단계 |
| RGB전처리 | 영상배치형식미확인 | observation_contract.py:77 rawRGBuint8확인,87 state/task,89 CHW/배치float/255 | 저장processor rename/통계와연결 |
| 실행entry | 영상lerobot-record정책평가 | stream_live.py:178 독립stream,789 prepare→motion_ready→stream | 공식eval경로와다름 |
| chunk | 영상ACT세부수치미확인 | metadata config.json:54/55=50/50,process_policy.py:85 강제검사 | 모델출력50개묶음사용 |
| 시간·속도 | 영상서보Hz미확인 | stream_live.py:22 MODEL_HZ30,23 speed0.33,24 CONTROL_HZ60,183 rate=30×0.33=9.9;325 16ms초과시50Hzfallback | action진행9.9/s와wire50/60Hz는서로다름 |
| 비동기 | 영상세부미확인 | stream_live.py:208 pool1worker,268 submit,239 chunk_alignment,61 overlap merge | 추론중이전chunk실행·만료앞부분skip·겹침blend추가 |
| 보간 | 영상미확인 | stream_live.py:76 fraction²(3−2fraction) | **cubic smoothstep**,quintic minimum-jerk아님 |
| home보간 | 영상미확인 | live_start.py:103–113 quintic곡선 | home복귀와일반stream보간을혼동하면안됨 |
| RTC | 영상미확인 | stream_live.py:751 rtc=False;stream_policy.py:17/25 조건분기 | 현재모델RTC guidance비활성. runner시간skip은별도 |
| 실제목표 | 영상추가변형미확인 | stream_live.py:31 GripRelief,286 relief.apply;direct_live.py:278–294 clip/unnormalize/raw정수제한/sync_write | 모델출력그대로전송하는것아님 |
| 온도수집 | 영상미확인 | stream_live.py:459 bulk수집후개별63,495 checked55gate;thermal_read.py 개별재독max | 영상과동일온도/통신부하라고볼수없음 |
| 시작범위 | 영상미확인 | direct_live.py:17 elbow상한3082;live_start.py 시작판정공유;시작raw상한3083 | home±2정렬과전체시작조건분리 |

표의process_policy는 `reports/v6_process_isolation_20261105/operator_candidate/process_policy.py`,observation_contract/policy_api는 `reports/dry_rearm_20261105/`의로컬배포후보/계약소스다. 실제import경로가동일한지는총괄의배포기록과대조할항목이다. metadata가현재선택checkpoint와동일함도배포지문으로재확인할수있지만이번작업에서원본/학습자료를변경하지않았다.

## 4. 문제별 해석과 총괄 확인 항목

**온도:** OFF/SDK없는raw63에서도gross스파이크가남으므로ACT로바꾸거나공식record로바꾸면해결된다고말할수없다. custom runner의추가6~12회개별READ는통신부하차이를만드나그차이가필수원인이라는증거는없다. 영상에서온도오류가안보인다는사실도그환경에오류가전혀없었다는검증은아니다. 별도온도조사결과와분리한다.

**위치추종/작업:** 모델종류·시야·데이터·task·보정/정규화·checkpoint가다르며추가속도/보간/그리퍼변형도있다. 따라서모델원출력,변형후wire목표,주소67active target,주소56present를같은시간축에서분리해야한다. 단순span비율이나모델action진행속도를실제추종률로취급하지않는다. 지금코드읽기만으로어느변형이실패원인인지판정할수없다.

총괄에는영상화면에서실제policy.type/정확한CLI/프레임·dataset.fps/use_degrees표시가보이는지확인해달라고요청한다. 영상에없는값은미확인으로유지한다. 향후동일checkpoint의표준경로와custom경로출력비교는장치없는단계부터설계할수있지만이번요청에서실행/전환/재학습/실기시험은하지않았다.

## 파일 근거와 지문

작업루트 `C:/Users/User/Documents/ChatGPT/자율설계/` 아래:

- `tmp/vla_direct_live_v3_20261109/stream_live.py`: SHA256 `91f83d1f9ce62941ba1819d8cba3c30b2f48e806074148bb757abfad645c97e0`
- `tmp/vla_direct_live_v3_20261109/stream_policy.py`: SHA256 `6617d2a82e20da2396c5a342f5f7271a86565e146e56b3d56a0a8550bbe0321f`
- `reports/overnight_20261104/model_metadata_20261105/config.json`: SHA256 `75cfa6dbe5c79f316811c2b9f6b624faade1e1621b85a37af8d79fc5beab4ced`
- 같은metadata폴더 `train_config.json`, `policy_preprocessor.json`, `policy_postprocessor.json`
- `tmp/vla_direct_live_20261109/direct_live.py`, `tmp/vla_direct_live_v3_20261109/live_start.py`
- 자막원문: `C:/Users/User/AppData/Local/Packages/openai.codex_2p2nqsd0c76g0/AC/#!sandbox.{ef5fea93-bfa8-4685-b328-ff5fea9684f8}/Temp/browser-use/exports/youtube-ElZvzKRt9g8-f4f52247-d91a-421d-ad69-bd4334bbd1c3.txt`

생성파일은이보고서1개뿐이며런타임/SSH/장치/추론/체크포인트수정은없다. 웹출처는작성자문서·작성자GitHub·HF공식소스만사용했다. PyBrain개인GitHub는독립확인하지못했으며RoboSEasyGitHub로대체동일시하지않았다.
