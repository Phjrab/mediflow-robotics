# 11월 SmolVLA 학습 결과와 데이터 구성

## 학습 범위

새 장소의2026-11-02~03 약통A 시연을 사용했다. 과거 장소 A-v2 60회, 예전200step/1,763step 결과는 보존 대상으로 분리했으며 이번3색 성능 근거로 사용하지 않는다.

최종 repo_id: `local/a_red_green_blue_137_exclude10_20261103`.

|실험|학습 데이터|본학습|기록된 결과|
|---|---|---|---|
|빨강·초록|93회/44,119프레임|20,000step|종료/체크포인트/저장 샘플 재로딩 확인 기록|
|최종3색|137회/63,551프레임|20,000step|빨강48/초록45/파랑44, 저장 샘플3색 action[1,6] 유한값 기록|

3색은 빨강·초록20K 모델을 초기 가중치로 사용하되 새optimizer와 별도출력 경로로 학습했다. 두 run을 같은run의40Kstep으로 표현하지 않는다. RTX3060, batch4, seed101, chunk_size/n_action_steps50,5,000step 저장, BF16 사용이 인계기록에 있다. 실제 로봇 성공률은 미측정이다.

## 여섯 원본과 제외13회

번호는 원본 데이터셋 내부에서1부터 센 회차이며 병합index가 아니다.

|원본 데이터셋(계정 Supermassive111)|실제 색|원본|제외 회차|학습 사용|
|---|---|---:|---|---:|
|a_to_red_20261102_201029|red|10|2|9|
|a_to_red_20261102_210433|red|40|36|39|
|a_to_green_20261102_201804|green|10|없음|10|
|a_to_blue_20261102_212958|green(이름과 다름, 사용자 확인)|40|5,21,22,29,38|35|
|a_to_blue_20261103_193835|blue|41|1,4,5,10,21,35|35|
|a_to_blue_20261103_193353|blue|9|없음|9|
|합계||150|13회|137|

`exclude10`은 **파랑10번째 회차를 추가 제외한 버전**이며 총10개 제외라는 의미가 아니다. 해당 회차에서 수직 영상427프레임/데이터428행 불일치가 발견돼 회차 전체를 제외했다. 파랑14번째 재시도는 유지했다. 초록 원본10회의5·6번째 낙하 메모도 제외 사유로 쓰지 않았다.

빨강·초록 준비기록에는 별도 파생본에서 빨강 추가40의40번째 마지막 행1개와 초록 추가40의1번째 마지막 행1개를 정합성 때문에 제거한 이력이 있다. 원본은 보존했다. 제외 목록만 적용하면 최종 데이터가 완전히 재현된다고 가정하지 않는다. 첫 프레임 home 통계는 이 마지막행 조정과 별개다.

## task와 입력

```
Put medicine bottle A into the red basket
Put medicine bottle A into the green basket
Put medicine bottle A into the blue basket
```

|원본 데이터 키|모델 키|물리 시점|녹화 영상|
|---|---|---|---|
|observation.images.end_effector|observation.images.camera1|손목USB|640×480 RGB,30fps|
|observation.images.ceiling_vertical|observation.images.camera2|천장수직 Astra|640×480 RGB,30fps|
|observation.images.ceiling_oblique|observation.images.camera3|천장사선 RealSense|640×480 RGB,30fps|

저장 policy_preprocessor.json의 rename이 위 변환을 수행한다. 입력측에서 다시 이름을 바꾸지 않는다. config 모델 feature는3×256×256이며 원본640×480 형상과 구분한다. 카메라 이름을 사전순으로 정렬해서 모델의 물리 대응을 새로 정하지 않는다.

state/action 순서: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper(`.pos` 이름). 원본 meta에는 단위 필드가 없다. 몸체degree/그리퍼0~100 판단은 use_degrees=True인 LeRobot 설정과 운영자가 확인한11월 녹화 경로를 근거로 한다. 값 범위만으로 단위를 추정하지 않았다.

## 모델 식별

최종 model.safetensors 크기:1,197,789,224 bytes. Windows 실제 파일 SHA256 계산값:

```
b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654
```

체크포인트는 config, model.safetensors, preprocessor/postprocessor JSON, 각각의정규화 safetensors, train_config의7개 파일로 전달됐다. 운영자가 Jetson에서7개 수신검증 PASS를 보고했다. 토크나이저/processor 캐시는 가중치파일과 별개이며 최종 모델 로딩 성공은 사용자 실행보고로 확인했다. 토큰/캐시/가중치는 이번 공개 문서에 포함하지 않는다.

## 보정 근거의 상태

과거10월 요약본의 해시를 현재Jetson에서 직접 읽은 값으로 혼동한 보고는 철회했다. 이후 운영자가11월녹화기기=Jetson, follower id=so-101, 보정파일수정시각2026-09-13 14:39 및미변경, 동일보정사용을 명시적으로 확인했다. 이 확인을 최신근거로 채택했으며 독립적인11월실행로그확보와구분한다. 노트북의9월soarm.json은 운영자결정에따라이번녹화와무관한자료로취급한다. 보정 원본과 원문 셸기록은 공개하지 않는다.
