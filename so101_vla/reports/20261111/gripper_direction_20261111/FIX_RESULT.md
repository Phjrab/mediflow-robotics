# 그리퍼 방향 수정과 검증

2026-11-11. **동기 녹화 영상으로 값 증가=열림,감소=닫힘을 확인해 조임 완화 코드의 반대 전제를 고쳤다.** 물리 방향의 원래 미확인 상태는 해소됐다. 온도 응답 이상 수리나 실제 집기 성공을 뜻하지는 않는다.

## 근거

두 세션의3개 에피소드,3쌍을 손목/사선2뷰와 같은 프레임의 follower state/action으로 대조했다. 총괄도 최종 이미지3개를 직접 확인했다. [동기 비교 보고서](DIRECTION_EVIDENCE.md),[정확한 행과 원본 지문](reviewed_pairs.json).

| 에피소드 | 넓게 열린 state/action | 거의 닫힌 state/action |
|---|---:|---:|
| trial0 |47.613/49.311|1.163/1.149|
| trial1 |39.963/40.046|1.224/1.225|
| main0 |44.308/44.717|1.163/1.225|

원본8개 파일은 읽기 전후 지문 동일. 데이터·모델 값이나 보정/drive_mode를 반전하지 않았다. 일반 SO100 kinematic processor 주석을 이 기구의 물리 방향으로 적용했던 판단이 잘못이었다.

## 바뀐 코드

- `tmp/vla_direct_live_v3_20261109/stream_live.py`: 열림은 증가 방향으로 인식하고 제한을 해제한다. 막힌 닫힘은 position-target차이로 판정하며0.5재생초 뒤 목표를 `max(target,position-4)`로 제한한다. cap 유지 방향도 대칭 수정했다. 기존5점/1점/0.5초/4점 기준은 바꾸지 않았다.
- `tmp/vla_direct_live_v3_20261109/startup_simulation.py`: 막힌 그리퍼 시나리오를 실제 방향에 맞게 변경했다. 먼저 정상 개방한 뒤 물체가 폐쇄를 막는 상황을 모의한다.
- 웹 `corrections.py`의+3점 열림 명령은 녹화와 일치해 변경하지 않았다.
- 55°C·상태 오류·통신 검증·범위·Ctrl+C·HOLD 조건은 변경하지 않았다. `thermal_read.py`는 수정 전후 동일 지문이다.

## 실행한 검증과 배포

- Jetson Python3.10 후보 `stream_live.py --mock`:13개통과,exit0. 열림 통과·닫힘 정체 완화·이동 중 닫힘 비오판·열림으로 전환 시 cap 해제·웹 열림과 방향 일치·기존중단시나리오 포함.
- 노트북 온도 경로11개와60/38/57재생2개 통과. 고온을 낮은 재측정값으로 지우지 않는다.
- Jetson실제모델·카메라3대 + 가짜모터 dry:`bash ~/vla_direct_live_v3_20261109/run.sh --dry-run --duration 12 --speed 0.33`,exit0,RUN_COMPLETE. `~/vla_camera_dry_1794397829934842355/summary.json`.
- dry12.001초/513모의목표/모델chunk3개/59.548Hz/묶음대기최대·합계0초/실제모터쓰기0/카메라정리오류0. 손목/수직/사선 실패0/0/3,최대갱신공백0.0458/0.0528/0.0497초. 그리퍼 막힘 완화는 별도의 모의 사례로 확인했고, 이 dry-run의 relief_steps는0이었다.
- dry시작요약의토크ON/34°C/state는가짜모터값이다. 실제센서해결이나실제토크ON을뜻하지않는다.

배포경로:`~/vla_direct_live_v3_20261109/stream_live.py`,같은폴더`startup_simulation.py`. 원본은 `~/gripper_direction_fix_20261111/backup/`와노트북 `.bak_20261111_gripper_direction`에보존. 배포영수증/모의로그/dry요약은 `../vendor_isolation_20261111/jetson_results/gripper/`에도있다. 초기배포검사에서수기로옮긴기준hash오타가검출되어복사전중단했고,실제원본과노트북백업hash를재대조한뒤두파일을배포했다.

현재지문: stream=`f9e1cb1078476636650962b058f5ad0448e0871dd5c69b4b07aa8c6118e0545f`,simulation=`4850f805ca6f66f4f74d627491c9706efd34dbc3941f031a55e75e307f98739b`. 보정SHA는 `2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e`로동일.

실제 그리퍼 이동·live는 이번에 실행하지 않았다. 방향 로직의실제물체조임완화는후속현장검증항목이다. 온도 원인은 [제조사SDK조사](../vendor_isolation_20261111/RESULT.md)를따른다.
