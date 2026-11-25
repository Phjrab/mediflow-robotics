# 약통 A·B·C × 바구니 색 데이터 인벤토리

2026-11-25 KST. Windows 작업공간·프로젝트 폴더·로컬 LeRobot 캐시, 학습 서버 chosun의 읽을 수 있는 파일, Jetson의 보존 캐시, 접속 계정에서 조회 가능한 Hugging Face Hub를 조사했다. 새 실물 동작이나 촬영은 하지 않았다.

**현재 최종 137회 모음에는 약통 A만 있다.** 원래 과제의 색 문구를 사용한 시연은 빨강 48회, 초록 45회, 파랑 44회다. **B와 C의 별도 행동 데이터도 보존되어 있다.** 9월 C 자료 60회는 과제명이 *basket C*지만, 실제 사선 영상에서 **녹색** 바구니로 끝난다. 10월의 *C→빨강* 규칙·텔레옵과 다른 수집 조건이다. 촬영 시기와 배치를 지운 채 문자 칸을 색으로 일괄 변환하면 서로 다른 목적지를 같은 라벨로 학습하게 된다.

## 9조합

숫자는 서로 겹치지 않는 하나의 명시된 묶음 안에서만 읽는다. **색 과제**는 보존된 텍스트 라벨, **종료 배치**는 보존 영상의 실제 색, **이전 판독**은 당시 문서에 기록된 별도 시연이다. 빈 조합은 이번 조사에서 독립적으로 입증된 색별 행동 회차 수를 확정할 수 없다는 뜻이다. 모든 파일을 통틀어 존재하지 않는다는 의미로 쓰지 않았다.

| 대상 | 빨강 | 초록 | 파랑 |
|---|---|---|---|
| A | 11월 색 과제 48회(고정 train 41 / eval 7). 대표 회차 종료 우측 빨강 확인. 별도 9월 A v1 문자 묶음 59/60 우측 빨강 관찰 | 45회(38 / 7). 대표 종료 가운데 초록 확인 | 44회(37 / 7). 대표 종료 좌측 파랑 확인. 별도 10월 A v2 문자 묶음 59/60 우측 파랑 관찰. 별도 A→파랑 텔레옵 기록도 있음 |
| B | 색별 행동 회차 미확정 | 10월 7일 B→초록 수동 1회 보고, 해당 참고 텔레옵 영상 594장 이전 판독. 9월 B 문자 자료와 배치·증거가 다름 | 9월 B문자 원본 60회 중 59회에서 가운데 연파랑(ID6) 투입 관찰. 7프레임 ep45는 미판정 |
| C | 10월 7일·8일 C→빨강 텔레옵의 종료 사진을 당시 육안 판독. 각 578장·251장, 11월 데이터와 별개 | 9월 28일 C 문자 과제 60회. 60/60의 최종 배치가 왼쪽 녹색. 하단 확인 방법 참조 | 색별 행동 회차 미확정 |

보존된 행동 시연의 **명시적 색 과제 또는 영상으로 목적지를 직접 확인한 조합은 5종**(A→빨강·초록·파랑, B→파랑, C→초록)이다. 10월 문서가 별도 시연을 보고한 B→초록·C→빨강까지는 출처가 있으나, 같은 형태의 3RGB+6축 행동 데이터로 취합할 수 있는지와 장면별 정렬은 별도 검증 대상이다. B→빨강·C→파랑은 검사한 범위에서 개별 행동 회차를 확보하지 못했다.

137회 고정 분할은 전체 116/21회이고, 21회에는 색별 7회가 있다. 초기 배포 SmolVLA 학습에는 137회 모두가 쓰였다. 11월 녹화의 동일한 세 약통이 화면에 보인다는 사실만으로 B·C 목표 시연이 추가되지는 않는다. 위 9조합의 수치를 더해 독립 세션·실물 성공률 또는 합쳐진 검증셋의 크기를 계산할 수 없다. [사용 회차·고정 분할](model_compare/split.json)

### 보유한 A 색 명령 원본과 최종 채택분

10월 29일~11월 3일의 명시적 색 지시 원본을 **저장소 ID + source episode index**로 셌다. Hub·Jetson·Windows·학습 서버에 같은 ID가 있는 경우 한 번만 포함했고, 병합·수선·분할 사본은 제외했다. 서로 다른 원본 ID가 같은 장면을 재사용했는지 전 프레임 대조한 값은 아니다. 따라서 아래 ‘원본’은 보유 녹화 레코드 수이며, 별도 촬영 세션 수나 모두 성공한 회차 수를 뜻하지 않는다.

| 표적 색 | 0회 초과 원본 ID 수 | 보유 회차 / 프레임 | 137회 모음 채택 회차 / 프레임 | 원본에만 남은 회차 |
|---|---:|---:|---:|---:|
| 빨강 | 2 | 50 / 23,955 | 48 / 22,752 | 2 |
| 초록 | 3 | 52 / 23,488 | 45 / 21,367 | 7 |
| 파랑 | 6 | 64 / 28,928 | 44 / 19,432 | 20 |
| 합계 | 11 | 166 / 76,371 | 137 / 63,551 | 29 |

파랑 64회에는 10월 29일 파일럿 2회, 11월 2일 main 1회·203057 1회·203209 10회, 11월 3일 9회·41회가 포함된다. 초록 52회에는 파일명에 blue가 붙은 212958의 40회와 별도 212758의 2회가 포함된다. 166회 중 생산 학습의 원재료로 선택한 6개 ID가 150회이고 그 안에서 다시 13회를 제외해 137회가 됐다. 문자 바구니 지시·후대 교시 trace와 이 표의 수는 더하지 않았다.

## 무엇을 직접 확인했나

- **11월 A 표본.** 천장 사선의 시작·중간·끝을 확인했다. 빨강 원본 201029 ep0 프레임 0/275/550, 초록 원본 212958 ep0 프레임 0/258/516, 파랑 병합 ep93 프레임 0/219/437이다. 모두 시작 시 약통 3개와 세 칸, 영상 왼쪽부터 파랑·초록·빨강이 보인다. 종료 시 각각 오른쪽 빨강·가운데 초록·왼쪽 파랑에 약통이 들어 있다. 직접 본 3회의 물리 배치를 전체 원본 모든 프레임에 일반화하지 않았다.
- **9~10월 A·B 문자 과제 전량.** Jetson 보존본 세 묶음 180회의 사선 시작/끝 360프레임을 모두 디코딩해 축소 판독했다. 가림·초기 바구니 점유·리셋 사례는 사선·수직의 중간 시점도 읽었다. 바구니 재질의 물리 색은 사선에서 판독하고, 색 편향이 있는 수직 영상은 위치·움직임 보조에 사용했다. 초기 표본 ep0·30·59 모두 시작에 약통 3개와 바구니 3개가 보인다.
  - **9월 A v1**은 좌→우 초록·연파랑·빨강. 59/60회에서 오른쪽 빨강(ID4) 관찰, ep20은 7프레임뿐이어서 미판정. ep0·30·59의 마지막 프레임은 521·236·568이다.
  - **10월 A v2**는 좌→우 빨강·초록·연파랑. 59/60회에서 오른쪽 파랑(ID6) 관찰. 마지막 프레임에서 확인한 58회에 ep21의 중간 투입 1회를 더했다. ep21은 프레임 671·755에서 바구니에 보이고 839까지 사람이 다시 놓았다. ep12는 미판정이다. ep54·55는 초기 바구니 점유 후 재배치, 마지막에 투입했다. ep0·30·59의 마지막 프레임은 885·436·425다.
  - **9월 B v1**은 좌→우 초록·연파랑·빨강. 59/60회에서 가운데 파랑(ID6) 관찰, ep45는 7프레임뿐이어서 미판정. ep20은 시작 때 바구니가 차 있으나 프레임 185까지 비워졌고 370 이후 다시 넣었다. ep39는 약통 일부와 접근 장면을 함께 확인했다. ep0·30·59 마지막 프레임은 961·435·418이다.

두 A 묶음은 같은 *basket A* 문자 지시라도 관찰된 색이 달랐다. 9월 B 시연의 파랑은 10월 B→초록 정책·수동 시연과도 다르다. 이 수치는 보존 회차 내 관찰 건수이며, 사람 재배치가 포함된 사례가 있어 자율 정책 성공 횟수로 세지 않았다. [180회 원본의 판독 방법과 예외](command_task/jetson_cohort_basket_colors.md) · [회차별 기록](command_task/jetson_cohort_basket_colors.json)

- **9월 공개 C 전량.** 고정 리비전 84a6456b의 사선 MP4 file-000·001(합계 331,149,290바이트)과 episode 메타데이터로 사선 카메라의 실제 파일·시작 PTS·길이를 찾아 ep0–59 각각 첫 프레임과 마지막 프레임을 디코딩했다. 120장 전수 판독에서 왼쪽 초록·가운데 연파랑·오른쪽 빨강 순서였다. 끝 프레임 60개에서 왼쪽 초록에 약통 한 개가 보인다. 가림·눕힘·초기 상태를 더 확인하려고 ep5/16/24/26/28/50에 중간 프레임도 확인했다. 예를 들어 ep26은 프레임 352에서 놓인 약통이 보이고 469에서는 몸통 일부가 벽에 가려진다. ep16은 시작 때 이미 초록 바구니에 약통이 있으며 프레임 277에는 그리퍼에 들려 있다. **60은 C 라벨을 가진 녹화의 종료 배치 수**다. 정상 초기화에서의 60회 독립 성공을 뜻하지 않는다. 여러 시작 프레임에서 세 약통의 크기·위치 순서가 달라졌다. 모든 회차의 물체 신원을 크기나 OCR로 다시 분류하지 않았다.
- **10월 별도 배치.** 10월 7일 사용자가 확인한 정책은 큰 A→파랑 ID6, 중간 B→초록 ID5, 작은 C→빨강 ID4다. 10월 17일 고정 데모에서는 A/B/C가 slot 1/2/3, 약통 태그 9/8/7이었다. 사용자는 1개·2개·3개와 3개 동시 분류 성공을 보고했다. 당시 Git 트리에 동기 영상·관절 행동 데이터는 없고 조합별 시도 수·실패 수는 기재되지 않았다. 해당 커밋은 교시 JSON·artifacts·raw logs/reports를 제외한다. Jetson의 별도 보존 프로젝트에는 10월 26일 mtime의 완성 교시 JSON이 있으나 당시 동기 trajectory로 확인할 수 없다. 색→태그 규칙, 9조합 명령 파싱, 사용자 성공 보고와 9조합 행동 시연은 각각 다른 증거다. [10월 17일 소스 기록](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/b0547ecb2ec0dde02f080c85ccffa894e80f2633/docs/sessions/2026-10-17-auto-web-demo.md)
- **10월 이전 판독의 범위.** 10월 7일 운영자는 A→파랑·B→초록·C→빨강을 각 1회 수동으로 완료했다고 기록했다. A→파랑 367장, B→초록 594장, C→빨강 두 회차 578·251장은 기존 천장 JPEG 시퀀스 판독 기록이다. C→빨강 10월 9일에는 별도 완주 텔레옵과 관절 경로도 문서화했다. 수신시각 기반 카메라 기록은 노출시각과 관절행동 간 학습용 동기를 보장하지 않는다. 이 JPEG 수나 서보 주기 수를 30 fps 동기 action 행 수로 환산하지 않았다. [10월 7일 수동 시연 기록](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/b0547ecb2ec0dde02f080c85ccffa894e80f2633/docs/sessions/2026-10-07-c-red-manual-placement.md) · [10월 8일 색별 시연·프레임 기록](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/b0547ecb2ec0dde02f080c85ccffa894e80f2633/docs/sessions/2026-10-08-c-transfer-vision-qa.md) · [10월 9일 C→빨강 시연](https://github.com/gyeyeongjo-lgtm/so101-lelab-medicine-sorter/blob/b0547ecb2ec0dde02f080c85ccffa894e80f2633/docs/sessions/2026-10-09-c-red-complete-teleop-demo.md)

## 경로와 과제 원문을 읽는 법

개인 홈 경로를 생략했다. **W**는 현재 Windows 프로젝트 작업공간, **J**는 Jetson의 `~/.cache/huggingface/lerobot/`, **T**는 chosun의 `~/Documents/Codex/2026-11-03/https-github-com-phjrab-mediflow-robotics/work/`, **M**은 같은 서버의 `~/MediFlowCompare/`, **L**은 Windows의 `~/.cache/huggingface/lerobot/`이다. 이름에 붙은 8자리 날짜는 수집 폴더 이름의 날짜인 경우가 많으며, 프레임별 실제 노출 날짜를 인증하는 시각은 아니다. 제어 파일이 없는 곳의 병 수·개별 색·배치는 미검증이다.

| 표의 과제 키 | tasks.parquet / tasks.jsonl의 원문 |
|---|---|
| AR | Put medicine bottle A into the red basket |
| AG | Put medicine bottle A into the green basket |
| AB | Put medicine bottle A into the blue basket |
| A문자 | Pick up medicine bottle A and place it into basket A. |
| B문자 | Pick up medicine bottle B and place it into basket B. |
| C문자 | Pick up medicine bottle C and place it into basket C. |

**3RGB**는 `observation.images.ceiling_vertical`, `observation.images.ceiling_oblique`, `observation.images.end_effector`의 세 시점이다. 11월/문자 과제 메타데이터는 각 시점 640×480 및 30 fps다. 표의 MP4 수는 물리 파일 수이며 한 MP4가 여러 회차를 담을 수 있다.

## Hugging Face Hub의 실제 원본 목록

공식 HfApi의 인증된 whoami 및 계정별 list_datasets를 조회했다. 프로젝트 데이터 소유자 Supermassive111의 저장소 11개가 나왔고 연결 계정 geonhoo가 소유한 것으로 반환된 저장소는 0개였다. 각 저장소의 dataset_info 리비전·파일목록, 고정 리비전의 info.json·과제·회차 메타데이터를 별도 확인했다. 각 링크는 검사한 리비전이다. 같은 시점의 전체 Hub 이름 검색에서 medicine_b_to_basket_b, medicine_a_to_basket_a, medicine_abc_matching_baskets 문자열로 추가 공개 목록은 나오지 않았다. 동일 ID의 로컬·Jetson 복제는 중복 집계하지 않는다.

| Hub ID (소유자 Supermassive111) | 회차 / 프레임 | fps · 시점 | 과제 | MP4 / 행동 parquet | 137회에 채택 |
|---|---|---|---|---|---|
| [medicine_c_to_basket_c_3cam_test_20260928_154245](https://huggingface.co/datasets/Supermassive111/medicine_c_to_basket_c_3cam_test_20260928_154245/tree/84a6456bfdaacda145c975ee7bab91d81872602c) | 60 / 28,966 | 30 · 3RGB | C문자 | 7 / 1 | 0 |
| [test10-291_20261029_205247](https://huggingface.co/datasets/Supermassive111/test10-291_20261029_205247/tree/831c150add1779848470df8aa42e26071290210c) | 2 / 1,053 | 30 · 3RGB | AB | 3 / 1 | 0 |
| [a_blue_main_20261102_192441](https://huggingface.co/datasets/Supermassive111/a_blue_main_20261102_192441/tree/7bbdea944abd1792549383c3d31cc555d637d618) | 1 / 653 | 30 · 3RGB | AB | 3 / 1 | 0 |
| [a_to_red_20261102_201029](https://huggingface.co/datasets/Supermassive111/a_to_red_20261102_201029/tree/c14477c22cb0cf3b27599a32b96222631380720e) | 10 / 4,628 | 30 · 3RGB | AR | 3 / 1 | 9 |
| [a_to_green_20261102_201804](https://huggingface.co/datasets/Supermassive111/a_to_green_20261102_201804/tree/ecc9d395cdce6bcc8a44ffbd6840ebe2f2d2466d) | 10 / 5,510 | 30 · 3RGB | AG | 3 / 1 | 10 |
| [a_to_blue_20261102_203209](https://huggingface.co/datasets/Supermassive111/a_to_blue_20261102_203209/tree/91da17d4ad12e361874acfcd12c2f6f4a68ffbff) | 10 / 4,011 | 30 · 3RGB | AB | 3 / 1 | 0 |
| [a_to_red_20261102_210433](https://huggingface.co/datasets/Supermassive111/a_to_red_20261102_210433/tree/25fe52d196abaf488ccd273eedb330e5334eb177) | 40 / 19,327 | 30 · 3RGB | AR | 5 / 1 | 39 |
| [a_to_blue_20261102_212958](https://huggingface.co/datasets/Supermassive111/a_to_blue_20261102_212958/tree/e4e70edd41a742ea12193faf96a9ecf55f01faea) | 40 / 16,752 | 30 · 3RGB | AG | 5 / 1 | 35 |
| [a_to_green_20261102_212758](https://huggingface.co/datasets/Supermassive111/a_to_green_20261102_212758/tree/fb8736965e280db4e4d5558af5363d82b416adec) | 2 / 1,226 | 30 · 3RGB | AG | 3 / 1 | 0 |
| [a_to_blue_20261103_193835](https://huggingface.co/datasets/Supermassive111/a_to_blue_20261103_193835/tree/75544709366178c94a0d397f0807820b7afcac00) | 41 / 19,154 | 30 · 3RGB | AB | 5 / 1 | 35 |
| [a_to_blue_20261103_193353](https://huggingface.co/datasets/Supermassive111/a_to_blue_20261103_193353/tree/7f78cf23974851a99dbdfe2430b6bfd587def8bd) | 9 / 3,643 | 30 · 3RGB | AB | 3 / 1 | 9 |

주의할 원본은 **a_to_blue_20261102_212958**이다. 폴더 이름에는 blue가 있으나 tasks.parquet·회차 과제 및 확인한 저장 영상은 초록을 가리킨다. 40회 중 최종 병합에는 초록 35회가 쓰였다. 세 색 최종 모음의 원재료 여섯 원본은 150회이며 13회를 제외한 137회만 사용했다. 별도 파일럿 및 남은 Hub 원본을 더해 137회의 새 시연으로 세지 않았다.

아래 현장 참조 ID는 같은 공식 세션에서 개별 dataset_info 호출이 **HTTP 404**였다. 삭제·개명·권한 제한 중 어느 것인지는 이 응답만으로 구분할 수 없다. 실제 Jetson 보존본 일부는 다음 절에 있다.

- `Supermassive111/medicine_a_to_basket_a_3cam_v1_20260928_211312`
- `Supermassive111/medicine_b_to_basket_b_3cam_v1_20260928_203749`
- `Supermassive111/medicine_abc_matching_baskets_3cam_v1`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v3_48episodes_20261006`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v3_trim05_20261006`
- `Supermassive111/Supermassive111_medicine_b_to_basket_b_3cam_v1_20260928_203749`

## Jetson 보존 원본 및 별도 시연

읽을 수 있는 홈에서 찾은 LeRobot info.json 64개를 모두 검사했다. 회차가 1개 이상인 경로 28개, 0회 경로 36개다. 각 경로의 task와 카메라 이름을 읽었다. 이 64개 info에 ArUco라는 이름을 가진 별도 관측 열은 없었다. 영상 속 마커와 구조화된 ArUco 좌표 관측 열은 다르다.

| J 아래 경로 | 회차 / 프레임 | fps | 시점 | 과제 원문 또는 위 키 | MP4 |
|---|---|---|---|---|---|
| `Supermassive111/14_20260914_153943` | 14 / 9,244 | 30 | 312, camera_2 | 14 | 2 |
| `Supermassive111/1grper_20260912_182316` | 2 / 2,009 | 30 | 312, camera_2 | 1grper | 2 |
| `Supermassive111/20_20260909_200738` | 20 / 15,948 | 30 | 312, camera_2 | 20 | 2 |
| `Supermassive111/28_20260914_153007` | 14 / 10,761 | 30 | 312, camera_2 | 28 | 2 |
| `Supermassive111/30_20260914_155550` | 30 / 18,974 | 30 | 312, camera_2 | 30 | 3 |
| `Supermassive111/30try_20260909_194347` | 5 / 3,918 | 30 | 312, camera_2 | 30 | 2 |
| `Supermassive111/30try_20260909_195028` | 1 / 1,238 | 30 | 312, camera_2 | 30th | 2 |
| `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846` | 60 / 32,597 | 30 | 3RGB | A문자 | 7 |
| `Supermassive111/Supermassive111_medicine_b_to_basket_b_3cam_v1_20260928_203749` | 60 / 27,613 | 30 | 3RGB | B문자 | 6 |
| `Supermassive111/a_blue_main_20261102_192441` | 1 / 653 | 30 | 3RGB | AB | 3 |
| `Supermassive111/a_to_blue_20261102_203057` | 1 / 414 | 30 | 3RGB | AB | 3 |
| `Supermassive111/a_to_blue_20261102_203209` | 10 / 4,011 | 30 | 3RGB | AB | 3 |
| `Supermassive111/a_to_blue_20261102_212958` | 40 / 16,752 | 30 | 3RGB | AG | 5 |
| `Supermassive111/a_to_blue_20261103_193353` | 9 / 3,643 | 30 | 3RGB | AB | 3 |
| `Supermassive111/a_to_blue_20261103_193835` | 41 / 19,154 | 30 | 3RGB | AB | 5 |
| `Supermassive111/a_to_green_20261102_201804` | 10 / 5,510 | 30 | 3RGB | AG | 3 |
| `Supermassive111/a_to_red_20261102_201029` | 10 / 4,628 | 30 | 3RGB | AR | 3 |
| `Supermassive111/a_to_red_20261102_210433` | 40 / 19,327 | 30 | 3RGB | AR | 5 |
| `Supermassive111/camera_recovery_regression_20260908_20260908_170150` | 1 / 135 | 30 | 312, camera_2 | approved camera recovery regression | 2 |
| `Supermassive111/firstdata_20260908_171141` | 5 / 1,790 | 30 | 312, camera_2 | 1st | 2 |
| `Supermassive111/medicine_a_to_basket_a_3cam_v1_20260928_211312` | 60 / 27,519 | 30 | 3RGB | A문자 | 6 |
| `Supermassive111/medicine_c_to_basket_c_3cam_test_20260928_154245` | 60 / 28,966 | 30 | 3RGB | C문자 | 7 |
| `Supermassive111/open100_20260914_144019` | 42 / 29,313 | 30 | 312, camera_2 | open100 | 3 |
| `Supermassive111/opengrip50_20260913_165317` | 50 / 31,598 | 30 | 312, camera_2 | 50try | 4 |
| `Supermassive111/opengripper_20260912_181900` | 1 / 1,280 | 30 | 312, camera_2 | opengripper | 2 |
| `Supermassive111/test10-291_20261029_205247` | 2 / 1,053 | 30 | 3RGB | AB | 3 |
| `Supermassive111/test_9_27_8_50_20260927_205320` | 1 / 855 | 30 | 3RGB | C문자 | 3 |
| `Supermassive111/test_9_27_8_55_20260927_205551` | 5 / 1,971 | 30 | 3RGB | C문자 | 3 |

9월 27일 C 테스트 1회·5회와 9월 28일 60회는 별도 ID이며 콘텐츠 중복 여부를 검사하지 않았다. 테스트 6회의 실제 목적지 색은 미판정이다. A v1 60회/27,519프레임과 A v2 60회/32,597프레임 역시 별도 버전이며 서로 독립된 120회로 확정하지 않았다. 10월 5일 당시 참조 검사는 A v2 60회 중 59회, B 60회 중 59회, C 60회 중 60회를 usable reference로 분류했다. A의 48회/24,588프레임 선별본 및 60회/27,564프레임 선두 트림본은 A v2의 파생이다. [ABC 당시 레퍼런스·과제 원문](../outputs/openclaw_aruco/dataset_reference_abc_20261005.json) · [A v3 48회 파생](../outputs/offline_act_a_v3_48episodes_20261006/dataset_validation.json) · [A v3 trim 파생](../outputs/offline_act_a_v3_trim05_20261006/dataset_validation.json)

**0회인 Jetson 경로** (모두 0프레임, 행동 시연 수에 제외)

- `Supermassive111/00_20260905_174810`
- `Supermassive111/11111_20260906_163342`
- `Supermassive111/11_20260905_215352`
- `Supermassive111/11_20260906_130206`
- `Supermassive111/11_20260906_143221`
- `Supermassive111/11_20260906_151533`
- `Supermassive111/11_20260906_154449`
- `Supermassive111/11_20260906_161629`
- `Supermassive111/11_20260906_162325`
- `Supermassive111/12_20260906_185122`
- `Supermassive111/1_20260905_215319`
- `Supermassive111/321_20260906_161838`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_172539`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_172613`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173126`
- `Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173204`
- `Supermassive111/a_blue_main_20261102_140120`
- `Supermassive111/a_to_green_20261102_201727`
- `Supermassive111/a_to_red_20261102_200821`
- `Supermassive111/a_to_red_20261102_200929`
- `Supermassive111/codex_ab_nocam_20260906_112311`
- `Supermassive111/codex_ab_nocam_rxclear_20260906_114147`
- `Supermassive111/codex_boundary_nocam_20260906_145811`
- `Supermassive111/codex_cli_diag_20260906T203342+0900_20260906_203359`
- `Supermassive111/codex_combined_nocam_20260906_152145`
- `Supermassive111/codex_retry3_nocam_20260906_143009`
- `Supermassive111/codex_rx_recovery_nocam_20260906_154053`
- `Supermassive111/codex_trace_nocam_20260906_143935`
- `Supermassive111/eopn50_20260913_153725`
- `Supermassive111/port_map_regression_20260908_20260908_164019`
- `Supermassive111/t_20260908_162450`
- `Supermassive111/t_20260908_162526`
- `Supermassive111/test10-29_20261029_205045`
- `local/so101_conditional_trace_test_20260906_155328`
- `local/so101_individual_read_test_20260906_160429`
- `local/so101_packet_metadata_test_20260906_160205`

## 학습 서버 chosun의 보존 경로

CPU로 파일과 parquet 메타데이터를 조회했다. Windows 드라이브 디렉터리 검색에서 별칭을 포함해 64개 경로가 나왔고 사용자 홈 기준 경로 18개로 정리됐다. Documents and Settings, My Documents 등의 동일 작업 디렉터리 별칭을 중복 집계하지 않았다. 18개 모두 30 fps·3RGB이며 행동 parquet 행 수가 info의 프레임 수와 맞았다. raw는 녹화 복제본, alignment_repairs는 끝 프레임 수선, filtered/selected와 merged는 선별·병합 결과, standard/onehot은 고정 분할 또는 지시 인코딩 파생이다.

| 서버 경로 | 회차 / 프레임 | 과제 | MP4 |
|---|---|---|---|
| `T/red_green_20261103/alignment_repairs_v1/green_additional40_named_blue` | 40 / 16,751 | AG | 5 |
| `T/red_green_20261103/alignment_repairs_v1/red_additional40` | 40 / 19,326 | AR | 5 |
| `T/red_green_20261103/prepared_v3/filtered/green_additional40_named_blue` | 35 / 15,857 | AG | 5 |
| `T/red_green_20261103/prepared_v3/filtered/red_additional40` | 39 / 18,809 | AR | 5 |
| `T/red_green_20261103/prepared_v3/filtered/red_original10` | 9 / 3,943 | AR | 3 |
| `T/red_green_20261103/prepared_v3/merged` | 93 / 44,119 | AR / AG | 16 |
| `T/red_green_20261103/raw/green_additional40_named_blue` | 40 / 16,752 | AG | 5 |
| `T/red_green_20261103/raw/green_original10` | 10 / 5,510 | AG | 3 |
| `T/red_green_20261103/raw/red_additional40` | 40 / 19,327 | AR | 5 |
| `T/red_green_20261103/raw/red_original10` | 10 / 4,628 | AR | 3 |
| `T/three_color_20261103/blue_raw/blue_41` | 41 / 19,154 | AB | 5 |
| `T/three_color_20261103/blue_raw/blue_9` | 9 / 3,643 | AB | 3 |
| `T/three_color_20261103/prepared_137_v1/blue41_selected35` | 35 / 15,789 | AB | 5 |
| `T/three_color_20261103/prepared_137_v1/merged` | 137 / 63,551 | AR / AG / AB | 24 |
| `M/dataset/onehot/eval` | 21 / 9,842 | AR / AG / AB | 24 |
| `M/dataset/onehot/train` | 116 / 53,709 | AR / AG / AB | 24 |
| `M/dataset/standard/eval` | 21 / 9,842 | AR / AG / AB | 24 |
| `M/dataset/standard/train` | 116 / 53,709 | AR / AG / AB | 24 |

## 현재 Windows 작업공간 및 캐시

작업공간의 표준 LeRobot 경로 14개 및 로컬 HF 캐시 0회 경로 12개를 읽었다. 새 조사를 위해 받은 Hub 메타데이터 캐시는 아래 집계에서 제외했다. 추가로 Documents/Codex·Documents/GitHub·Desktop·Downloads와 별도 so101-vla 체크아웃에서 프로젝트 관련 파일명을 조회했다. 일부 비프로젝트·테스트 캐시는 읽기 제한이 있어 장치 전체 부재를 보증하지 않는다. W의 blue_41·blue_9는 숫자 행과 3시점 스키마만 있고 그 폴더에 MP4가 없다. 동일 원본의 영상은 Hub/chosun/J와 병합본에서 확인했다.

| W 아래 경로 | 회차 / 프레임 | fps · 시점 | 과제 | 보존 MP4 |
|---|---|---|---|---|
| `.local/blue_update_20261103_200703/blue_41` | 41 / 19,154 | 30 · 3RGB | AB | 0 |
| `.local/blue_update_20261103_200703/blue_9` | 9 / 3,643 | 30 · 3RGB | AB | 0 |
| `.local/model_compare/onehot/eval` | 21 / 9,842 | 30 · 3RGB | AR / AG / AB | 24 |
| `.local/model_compare/onehot/train` | 116 / 53,709 | 30 · 3RGB | AR / AG / AB | 24 |
| `.local/model_compare/standard/eval` | 21 / 9,842 | 30 · 3RGB | AR / AG / AB | 24 |
| `.local/model_compare/standard/train` | 116 / 53,709 | 30 · 3RGB | AR / AG / AB | 24 |
| `.local/recording_checks/main_20261102_193317` | 1 / 653 | 30 · 3RGB | AB | 3 |
| `.local/recording_checks/trial_20261029_210002` | 2 / 1,053 | 30 · 3RGB | AB | 3 |
| `.local/three_color_checks_20261102_204203/blue` | 10 / 4,011 | 30 · 3RGB | AB | 3 |
| `.local/three_color_checks_20261102_204203/green` | 10 / 5,510 | 30 · 3RGB | AG | 3 |
| `.local/three_color_checks_20261102_204203/red` | 10 / 4,628 | 30 · 3RGB | AR | 3 |
| `MediFlow_coord_20261119/tasks/MF-01/loader_preflight/dataset_v3` | 137 / 63,551 | 30 · 3RGB | AR / AG / AB | 24 |
| `MediFlow_coord_20261119/tasks/MF-01/recovered_sources/a_to_blue_20261102_212958` | 40 / 16,752 | 30 · 3RGB | AG | 5 |
| `MediFlow_coord_20261119/tasks/MF-01/recovered_sources/a_to_red_20261102_210433` | 40 / 19,327 | 30 · 3RGB | AR | 5 |

**L/geonhoo/의 9월 5일 초기화 경로** (12개 모두 0회·0프레임, tasks 없음)

`11_20260905_172810`, `12_20260905_164743`, `12_20260905_172100`, `13_20260905_172346`, `1_20260905_164522`, `3_20260905_164805`, `4_20260905_164828`, `4_20260905_171058`, `5_20260905_164958`, `6_20260905_171137`, `7_20260905_171658`, `geonhoo_20260905_164425`

## 별도 이미지·교시 자료

| 위치 | 실제 단위와 내용 | 9조합 판단에 쓰는 범위 |
|---|---|---|
| Windows Desktop/so101_pilot_dataset_ready.tar.gz | 89,796,787바이트. 9월 21~22일 UTC. JPG 1,226장과 annotations.jsonl 1개, 주석 1,226행. answer의 A→basket_1 512장/B→basket_2 366장/C→basket_3 348장. 카메라 4종 | 한 장면 단위의 시각·응답 라벨. 시간 정렬된 6축 action/관측 회차로 합산하지 않음 |
| 저장소 dataset_v2 | source_records 1,226에서 before_grasp 후보 450개. A/B/C 205/112/133, train/validation/test 278/88/84, 준비 당시 검수 pending. 동일 세션 유사 이미지 포함 | 약통 인식 자료. 물체가 여럿 보이는 천장 영상일 수 있어 목표 위치·집기 처리를 직접 증명하지 않음 |
| 저장소 dataset_v3 | 중복 정리한 585장, 178세션, train/validation/test 375/99/111. A/B/C 200/218/167, BIN_A/B/C도 같은 수. before_grasp 383 / after_grasp 202 | 시각 지시/응답 프레임. 문자 BIN에 해당 색을 자동 대입하지 않음 |
| 10/17 Git 문서와 교시 | 문서의 규칙·사용자 보고, Jetson의 후일 mtime 교시 파일. 해당 커밋에서 MP4/parquet/NPZ/JSONL/AVI 0개 | 교시 자세·규칙은 타임스탬프별 3RGB+행동의 독립 시연으로 집계하지 않음 |

[dataset_v2 QA](../dataset_v2/qa_report.json) · [dataset_v3 QA](../dataset_v3/qa_report.json)
