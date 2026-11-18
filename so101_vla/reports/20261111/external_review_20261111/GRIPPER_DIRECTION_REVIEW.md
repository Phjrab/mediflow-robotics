# 그리퍼 개폐 방향 독립 읽기 검토

2026-11-11 KST. 생성파일은이보고서1개다. 로컬소스/기존메타/기존이미지만읽었고runtime/SSH/장치/추론/녹화/학습/보정변경은없다. 수치의크기만으로개폐방향을추정하지않았다.

## 결론

**총괄추가읽기근거반영:** Jetson설치 `~/venvs/lelab-jetson-py310/lib/python3.10/site-packages/lerobot/robots/so_follower/robot_kinematic_processor.py`의SHA256이 `b07b12fc84a93f3d535aea4e193faa371be2fdaed078318604ec4fe417582013`으로이담당이읽은휠내파일과일치하며409–417행주석/구현동일성을총괄이확인했다. 따라서**현재설치processor와휠의동일성은확인됨**으로갱신한다. 이담당이Jetson을직접조회한것은아니며총괄의읽기결과가근거다. 아래의물리기구/동기녹화방향미확인은그대로유지한다.

**물리그리퍼와녹화프레임을포함한방향확정은 UNVERIFIED.** 공식LeRobot0.6.0휠에는“joint position increases on close”라는명시적소스근거가있다. 그러나주석은SO100부호를명시하고현재설치SO101기구/보정/녹화와의동일성이완전히연결된증거는아니다. `RANGE_0_100`정규화만으로는열림/닫힘의물리의미를정의하지않는다.

**별도중요발견: 현재로컬코드내방향의미가상충한다.** `stream_live.py`의GripRelief는증가=닫힘,감소=열림으로취급하지만,같은state공간을쓰는`corrections.py`의`gripper_open`은현재값에+3점까지더한다. 적어도한쪽의명명/물리전제가불일치한다. 실제어느방향이맞는지는이검토로확정하지않았으며,임의로부호를바꾸지않았다. 이불일치는총괄에즉시전달했다.

## 소스 근거

### 1. GripRelief의 전제와 실제 동작

`tmp/vla_direct_live_v3_20261109/stream_live.py:38`은설치SOFollower kinematic processor가기준이라는주석을둔다. :39는`target < position`또는이전target보다감소하면opening으로분기하고cap을해제한다. :47–55는target이현재보다5점이상크고위치변화가1점이내인상태가playback기준0.5초지속되면cap=position+4를적용한다. 즉이완화로직은증가방향의닫힘/정체를억제한다는전제다. 전제가실기와반대면실제열림을막고실제닫힘에는완화를해제할수있다.

### 2. 공식 0.6.0 휠의 명시적 개폐 정의

기존로컬 `.local/lerobot-source-check/lerobot-0.6.0-py3-none-any.whl`을zip읽기로열었다. 설치/import/실행/압축해제파일생성은하지않았다.

내부 `lerobot/robots/so_follower/robot_kinematic_processor.py:369`의`GripperVelocityToJoint`:

- :385는discrete `{0=close,1=stay,2=open}`를정의한다.
- :409–412는SO100 sign을이유로“joint position increases on close”라고명시하고close→양의속도,open→음의속도로변환한다.
- :415–417은`delta=velocity*speed_factor`,현재position에delta를더해0–100으로clip한다.

이것은숫자범위에서추정한것이아닌명시적개폐코드근거이며GripRelief주석과논리적으로일치한다. 총괄이현재Jetson설치파일의동일hash와409–417행동일성을추가확인했다. 그러나이processor가현재custom joint-action경로에서실제로사용된다는뜻은아니다. current runner는정규화관절목표를직접받는다. **설치소스동일성은확인됐으나실제기구/녹화와의방향연결은미확인**이므로“실기방향까지검증됨”이라고쓸수없다.

같은휠내 `lerobot/robots/so_follower/so_follower.py:59`는gripper모터를`RANGE_0_100`으로정의한다. 몸5축의DEGREES선택과그리퍼0–100은별개다.

### 3. 0–100 정규화가 증명하는 것과 못하는 것

기존설치소스사본 `reports/goal_incident_20261106/source_review/motors_bus.py:869–871`은raw보정범위를0–100으로선형변환하고drive_mode가참이면`100-norm`으로반전한다. :898–901의역변환도같은방향을적용한다. `feetech.py:95`의apply_drive_mode=True와보정사본 `so-101.json`의gripper drive_mode=0/range413–2047을읽었다.

이사본에서는raw증가와정규화증가의수학적관계가보존된다. 하지만range_min/range_max는물리턱개방폭이나“open/closed”label이아니며,raw증가가실제턱을어느쪽으로움직이는지는별도기구증거다. 따라서413=닫힘/2047=열림또는그반대로수치만으로판정하지않았다.

`reports/nov_live_prep_20261104/units_so101.json:2–5`는6축순서,그리퍼percent_0_100,학습/현재보정hash동일성을기록한다. 그근거는파일에명시된운영자확인과소스다. **개폐방향확인의운영자발언이나동기영상label은없다.** 단위동일성과방향동일성은다르다.

### 4. 같은 로컬 코드의 반대 전제

`tmp/vla_direct_live_v3_20261109/corrections.py:35–43`:

- kind가`gripper_open`이면`target[-1]=min(current[-1]+MAX_GRIP_STEP,GRIP_CEILING,bounds상한)`이다.
- :16–17은MAX_GRIP_STEP=3,GRIP_CEILING=36이며오류문구도“개방한도”다.

GripRelief와corrections는동일`sample['state']`와정규화target공간을사용하며여기에방향을반전하는별도변환은해당분기에서없다. 따라서동일증가방향을한쪽은닫힘,다른쪽은열림으로명명한다는코드내상충은확인가능하다. 이사실만으로corrections가틀렸다고확정할수는없다. 실제물리방향이증가=열림이면GripRelief전제가틀릴수있고,증가=닫힘이면open교정명명/방향이틀릴수있다.

## 기존 녹화 프레임과의 대조 범위

`reports/main_recording_20261102_1934/inspection.json`은episode0/653frames/30fps/640×480/10.9초중간이미지3개를연결한다. 기존 `episode_000_end_effector_middle.png`를직접열어봤으며양쪽그리퍼패드사이에사각마커물체가있는장면을확인했다. 그러나그한프레임에는관절수치가없고,검수JSON에도해당timestamp의gripper state/action값이없다. 한장만으로열림과닫힘이진행하는방향을판정할수없다.

로컬data parquet와영상파일은존재하지만이번기본Python에는pyarrow가없다. 임의의새설치/새영상추출/원본변경은하지않았다. 별도의기존direction-labelled 동기2프레임쌍도이번읽기검색에서확인하지못했다. 따라서**학습녹화에서state/action증가가턱닫힘과대응하는지 UNVERIFIED**다. 이미지상물체가보이는것을gripper수치의방향증거로대체하지않았다.

충분한추가읽기증거는기존녹화의명확한open/closed또는이동중두시점에서동일ID/동일보정의state와action을동기화해비교한자료다. current state와leader/action은추종지연때문에즉시같지않을수있으므로각각분리해야한다. 이문서는새모터움직임이나토크ON을요청하지않는다.

## 온도 문제와 독립성

온도gross스파이크는토크OFF·목표쓰기0·LeRobot/SDK없는raw READ63에서도확인됐다. 그실험에는GripRelief target변형이나그리퍼개방교정이사용되지않는다. 따라서이부호전제가온도스파이크발생의필수원인이라는설명은성립하지않는다.

그리퍼의불필요한쥠/개방제한/작업실패에는방향전제가관련될수있으나,그것이이미관측된온도raw오류나팔꿈치추종오류의원인이라는증거는없다. **방향불일치·기계추종·온도표시오류는분리해서검증해야한다.** 이번검토에서안전55gate/필터/토크/목표/보정은변경하지않았다.

## 지문과 최종 상태

| 소스 | SHA256 |
|---|---|
| stream_live.py | 91f83d1f9ce62941ba1819d8cba3c30b2f48e806074148bb757abfad645c97e0 |
| corrections.py | 9fdadd7e51a2bd8a27e186e58ba50962c71e1406e063bd4453b4a9801bee33ab |
| 로컬공식0.6.0휠 | b38a564fbc441d98380576863bf68635dde5fc2c42ddc2a39d0486640dc9e9a8 |
| 휠내robot_kinematic_processor.py | b07b12fc84a93f3d535aea4e193faa371be2fdaed078318604ec4fe417582013 |
| 휠내so_follower.py | d09a2e63ad916f3a9e69740b1033e8c1f2dd5b025d61e5b0baacf1767ca6b8d1 |
| 보정사본so-101.json | 2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e |

최종판정: **공식휠의명시적증가=닫힘정의확인 / 현재설치processor와휠동일성확인(총괄읽기근거) / 현재코드내open방향상충확인 / 실제기구·동기녹화방향 UNVERIFIED.** 총괄에현재코드의상충과검증한계를전달하며단순주석만근거로부호수정을권고하지않는다.

## 최신 상태 인계 한계

총괄전달dry `vla_camera_dry_1794396158697105273`은12초/506모의목표/59.539Hz/wait0/실제쓰기0/카메라3대수신/사선초기실패3이다. 이담당이실행하거나원문을추가분석한결과가아니며,실물방향검증·센서수리·실기추종성공을뜻하지않는다.

**현재실제토크는이번읽기에서조회하지않아 UNVERIFIED. 최신live기록은ON유지였다는총괄정보가있다.** 이전OFF온도진단의상태를현재상태로이어붙여OFF로인계하면안된다. 이보고서수정은장치상태를변경하지않았다.
