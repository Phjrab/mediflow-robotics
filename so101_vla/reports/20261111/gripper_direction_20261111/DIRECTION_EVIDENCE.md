# 동기 녹화로 확인한 그리퍼 방향

## 결론: 이 녹화의 정규화 값 증가는 열림

서로다른두녹화세션의**3개에피소드·3쌍·각2뷰**를직접확인했다. follower `observation.state[5]`가약1점인프레임은패드사이가거의닫혀있고,약40–48점인프레임은명확히넓게열려있다. 저장된`action[5]`도각쌍에서같은방향으로작고커진다. 따라서**해당실물그리퍼와녹화좌표계에서는증가=열림,감소=닫힘**이라는물리적근거가충분하다. 숫자분포만으로판정한것이아니라같은frame의영상과관절값을맞춰육안검토했다.

낮은값장면을“완전0점물리접촉”,높은값장면을“최대100점개방”으로보정한것은아니다. 아래label은상대적으로명확한열림/거의닫힘이다. 그림에서좌측은넓게열림,우측은거의닫힘으로배치했으므로시간순서와다를수있다.

## 동기 비교 표

| 쌍/녹화/episode | 시각s | episode frame | 영상상태 | follower state[5] | 기록 action[5] |
|---|---:|---:|---|---:|---:|
| 1 trial/0 | 8.766666 | 263 | 넓게열림 | 47.613220 | 49.310871 |
| 1 trial/0 | 12.466666 | 374 | 거의닫힘 | 1.162791 | 1.148545 |
| 2 trial/1 | 6.366667 | 191 | 넓게열림 | 39.963280 | 40.045940 |
| 2 trial/1 | 0.000000 | 0 | 거의닫힘 | 1.223990 | 1.225115 |
| 3 main/0 | 8.700000 | 261 | 넓게열림 | 44.308445 | 44.716694 |
| 3 main/0 | 0.000000 | 0 | 거의닫힘 | 1.162791 | 1.225115 |

`state[5]`는저장follower관측값이고`action[5]`는리더시연에서기록된그리퍼목표이다. 별도의리더raw센서관측열을검증한것은아니다. 둘의순간값이완전히같지않은것은추종지연/관측시점등을포함할수있으며,이표는정밀추종오차평가가아니다. 같은에피소드의다른시점에서state와action각각의물리방향일치만확인한다.

### 쌍1: trial episode0

![trial episode0 열림/닫힘 동기 비교](C:/Users/User/Documents/ChatGPT/자율설계/reports/gripper_direction_20261111/pair_1_trial_ep0.png)

손목뷰에서두패드사이가좌측에서크게벌어지고우측에서거의붙는다. 사선뷰에서도분리/접근이일치한다. 팔자세가달라도손목카메라는그리퍼와함께움직이므로패드사이의상대간격을직접비교할수있다.

### 쌍2: trial episode1

![trial episode1 열림/닫힘 동기 비교](C:/Users/User/Documents/ChatGPT/자율설계/reports/gripper_direction_20261111/pair_2_trial_ep1.png)

좌측손목뷰는빈공간을사이에두고패드가넓게벌어졌고우측은패드가거의붙어있다. 사선뷰의물체를운영자가만지는장면은작업성공여부를판정하는데부적절하지만그리퍼간격방향을직접보는손목뷰의증거를무효화하지않는다. 이trial을성공시연으로선정했다는뜻은아니다.

### 쌍3: main episode0

![main episode0 열림/닫힘 동기 비교](C:/Users/User/Documents/ChatGPT/자율설계/reports/gripper_direction_20261111/pair_3_main_ep0.png)

별도날짜의main녹화에서도좌측패드간격이넓고우측은거의닫혀같은대응을보인다. trial한세션의영상우연이나에피소드경계오프셋으로만설명되는결과가아니다.

## 동기화와 원본 보존 검증

- 원본trial: `.local/recording_checks/trial_20261029_210002/`,episode0=416frames,episode1=637frames. 원본main: `.local/recording_checks/main_20261102_193317/`,episode0=653frames.
- 각녹화의data parquet에서episode/frame_index/timestamp/state/action을읽었다. episode별meta parquet의영상`from_timestamp`를읽어`absolute_video_frame=round(from_timestamp×30)+frame_index`로산출했다. trial episode1은오프셋13.866666667초=416frames이므로선정frame0/191은영상absolute416/607이다. 다른선정episode오프셋은0이다.
- ffmpeg를파일처음부터decode하고`select=eq(n,absolute_frame)`로정확한ordinal을선택했다. 빠른입력seek나시간반올림추출을사용하지않았다. `vsync=0`으로선정이미지를중복생성하지않았다.
- 선별6행모두`abs(timestamp×30−frame_index)<0.001`확인. 원본영상해상도640×480/30fps는기존inspection자료와일치한다. 데이터와카메라의하드웨어동시노출오차/센서latency를새로측정한것은아니다. 선택값이명확히다른개방상태이고다른에피소드/세션에서도일치해한프레임수준의지연으로부호가반전되는증거는없다.
- 수치로label을자동부여하지않고먼저총37후보프레임의손목·사선contact sheet6개를모두직접열어검토했다. 이후명확한3쌍으로선정하고최종pair이미지3개도직접열어검토했다.
- parquet2종×2세션+영상2뷰×2세션=**8개원본파일SHA256을읽기전/후대조하여전부불변**확인. 상세해시는 `reviewed_pairs.json`과 `synchronized_candidates.json`에있다. 원본데이터/기존deps/보정/체크포인트는수정하지않았다.

## 사용한 기존 런타임과 생성물

새wheel설치없이기존앱CPython3.12.14 `C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1110-778-0_26.1110.778.0_x64__3k8sg7r9htsxt/dependencies/python/python.exe`를사용했다. 기존 `.local/recording-inspect-deps`의cp312용pyarrow를sys.path로연결했고bundledPillow13.0.0을사용했다. ffmpeg는기존 `.local/recording-video-fallback/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe`다. pyarrow/ffmpeg는녹화파일만읽으며장치를열지않았다.

생성물은모두 `reports/gripper_direction_20261111/`아래다:

- `extract_evidence.py`: parquet/영상메타동기추출재현코드. 37후보×2뷰=74개원프레임PNG와6개contact sheet,JSON을생성. 실행exit0.
- `build_pairs.py`: 직접검토선정6프레임을3쌍×2뷰이미지로배치하고8개원본hash불변검사. 실행exit0.
- `synchronized_candidates.json`, `reviewed_pairs.json`:전체후보/선정행정확값·offset·absolute영상frame·원본hash.
- `pair_1_trial_ep0.png`, `pair_2_trial_ep1.png`, `pair_3_main_ep0.png`:최종리뷰이미지.
- 같은폴더의수정전.bak_타임스탬프는레이아웃개선전스크립트/이미지/JSON보존본이다.

## 총괄에 제안하는 코드 변경 방향 — 이 담당은 변경하지 않음

현재설치kinematic processor의SO100“증가=닫힘”일반주석은확인됐지만이녹화/기구의물리방향에는맞지않는다. 숫자/학습값을일괄반전하거나모터보정/drive_mode를바꾸면기존녹화와모델계약이깨질수있으므로그런변경을제안하지않는다.

`GripRelief`의의미적방향만실물에맞춰재검토할근거가있다:

1. **opening은target증가방향**으로인식해야한다. 이전target대비증가검사도같은방향이어야한다.
2. 막힌closing판정은현재position보다target이낮은쪽,즉`position−target`으로차이를측정해야한다.
3. closing완화cap은위쪽으로`min(target,position+4)`하는현방향이아니라낮아지는목표에대한하한 `max(target,position−4)`방향으로대칭검토해야한다. cap유지중갱신방향도같이검토한다.
4. `corrections.gripper_open`의증가방향은이번물리영상과일치한다. 단순히기존주석에맞춰open교정을감소로바꿔서는안된다.
5. 수정후에는열림통과·닫힘정체완화·실제계속닫히는경우·열림으로전환시cap해제·목표변화방향과현재위치관계가갈리는경우를장치없는테스트로확인해야한다. threshold/timing/토크/안전55gate는이번방향증거만으로변경할근거가없다.

이번증거는온도센서수리나팔꿈치추종문제해결을뜻하지않는다. 토크OFF·SDK없는raw온도스파이크는GripRelief가없는경로에서도발생하므로독립문제다. 현재실제torque는읽지않았으며이증거작업에서OFF로인계하지않는다. 런타임부호변경·실기검증·배포는총괄의후속범위다.
