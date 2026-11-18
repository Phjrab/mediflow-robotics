# 온도 급변 조사와 이번 검증 결과

2026-11-11. **실제 온도 응답 이상은 미해결이다.** 안전 검사와 실행 경로 검증 통과를 센서 수리 완료로 해석하지 않는다. 이번에는 실행기·보정·펌웨어를 변경하지 않았다. 실제 모터와 serial을 열지 않았고 모터 쓰기·live·push·삭제는 모두 NOT_RUN이다.

## 지금 확실한 것

- 최신 실기 `vla_stream_blue_1794394911695485729`는 381스텝 뒤 그리퍼 개별 온도 60/60°C로 중단됐다. 원문은 `ffff0603003cba`, 같은 시각 bulk 값은37°C, 종료 재측정은38/38/57°C였다. 열이 실제로 그 속도로 오르내렸다고 인정할 수 없는 관측이다.
- 앞선 토크 OFF·무동작·모델/카메라/SDK 없는 독립 READ63에서도36→53→36°C 같은 변화가 수십 ms에 재현됐다. 따라서 팔의 위치, 정책, 움직임 속도, Python의 섭씨 변환만이 필수 원인이라는 설명은 맞지 않는다.
- 호스트가 받은 응답의 ID·길이·상태·합 검사는 맞았다. 그러나 이 검사는 실제 물리 온도나 송신 이전/이후의 무결성까지 보증하지 않는다. 센서/ADC·모터 펌웨어·전원/참조전압·전기 통신 경로 중 정확한 위치는 UNVERIFIED다.
- 겉을 만져 차갑다는 운영자 관찰은 참고하되 내부 온도 측정의 대체 검증은 아니다. 실제 과열을 무시하면 구동부 손상·화상·예상치 못한 정지 위험이 있다. 60°C 표시가 즉시 로봇이 녹는다는 뜻은 아니다.

기존 원문은 `reports/thermal_deep_20261111/`과 `reports/live_review_1794394911695485729/RESULT.md`에 보존했다. 같은 읽기 실험을 해결될 때까지 반복하거나 높은 값을 낮은 재측정값으로 지우지 않았다. 55°C 중단은 그대로다.

## 외부 조사

[온도 조사](TEMPERATURE_RESEARCH.md): Robo9 커스텀 연구 펌웨어에도 온도/보호 신뢰성 경고가 있다. 그 펌웨어는 우리 공장3.10과 달라 같은 버그나 해결 버전으로 단정할 수 없다. 제조사 표는 주소63을 내부 온도로 정의한다. 공개 자료에서 우리3.10에 적용할 검증된 수정은 찾지 못했다. 펌웨어 플래시는 하지 않았다.

[영상 비교](TUTORIAL_COMPARISON.md): 실제 링크는 PyBrain EP.10이다. 26:30은 녹화 단계이며 이후 정책은 ACT, front/top2뷰다. 우리는 SmolVLA3뷰·별도 비동기 실행기·0.33배 재생·50/60Hz 보간을 쓴다. 큰 흐름은 같지만 영상 그대로는 아니다. 이 차이만으로 OFF 상태의 온도 원문 급변을 설명할 수 없다. 일반 stream 보간은 cubic smoothstep이며 home의 quintic minimum-jerk와 구별한다.

## 별도로 발견한 그리퍼 방향 불일치

[방향 검토](GRIPPER_DIRECTION_REVIEW.md): GripRelief는 증가를 닫힘으로 취급하지만 웹 gripper_open도+3점을 준다. 같은 정규화 공간에서 의미가 상충한다. 실제 설치 kinematic processor도 읽어 공식 휠과 같은 SHA256 `b07b12fc84a93f3d535aea4e193faa371be2fdaed078318604ec4fe417582013`임을 확인했다. 해당 코드의 SO100 주석은 증가=닫힘이다. 이 주석만으로 현재 SO101 기구의 개폐 방향을 확정하지 않았고 부호를 임의 수정하지 않았다. 기존 녹화의 동기화된 개방/폐쇄 프레임과 state 비교가 필요하다.

이 문제는 잘못된 교정·조임 완화에 영향을 줄 수 있으나 GripRelief가 없는 OFF/raw 온도 실험까지 설명하지는 못한다. 이번 Python은3.14이고 기존 pyarrow 바이너리는cp312라 로컬 동기 프레임 분석은 이 환경에서 실행되지 않았다. 기존 환경 설치·변경은 하지 않았다.

## 실제 실행한 검증

| 위치 | 명령 | 결과 |
|---|---|---|
| 노트북 | `python -B -m unittest discover -s tmp/vla_direct_live_v3_20261109 -p tests_thermal_read.py -v` | exit0,11개통과 |
| 노트북 | `python -B reports/thermal_deep_20261111/tests_actual_sdk_replay.py -v` | exit0,3개통과;실제SDK 소스·메모리 포트 |
| 노트북 | `python -B reports/thermal_deep_20261111/raw_temperature_probe.py --mock` | exit0,5개통과;장치미접근 |
| 노트북 | `python -B reports/external_review_20261111/tests_latest_thermal_trace.py -v` | exit0,추가2개통과;60/38/57 원문·중단유지 |
| Jetson | `bash ~/vla_direct_live_v3_20261109/run.sh --mock` | exit0,10개통과 |
| Jetson | `bash ~/vla_direct_live_v3_20261109/run.sh --dry-run --duration 12 --speed 0.33` | exit0,RUN_COMPLETE |

노트북 stdout/stderr·exit code는 `LOCAL_TEST_RESULTS.json`에 있다. 추가 테스트는 원문60/38/57을 임의 보정 없이 해석하고,60°C 중단 뒤38°C가 와도 정책 실행을 허용하지 않으며 HOLD 경로만 남는지 확인한다.

Jetson dry 결과: `~/vla_camera_dry_1794396158697105273/summary.json`. 실제 모델/카메라3대 + **가짜 모터·가짜 state/온도**다. 실제 로봇의 torque/온도를 읽은 결과가 아니다. 12.001초,모의목표506개,모델chunk3개,묶음대기최대/합계0초,모의전송59.539Hz,실제모터쓰기0,카메라정리오류0. 카메라 실패 손목0/수직0/사선3,최대공백0.0548/0.0443/0.0499초. 예열2.121/1.451초. 실제 온도 오류 해결 검증은 아니다.

dry 전 teleoperation/recording/inference 모두false 확인. 종료 뒤 LeLab/Astra 모두active,stream/진단/홈모니터 백그라운드 프로세스 없음 확인 후 SSH 종료. 이번 실제 motor torque는 읽지 않았으며 최신 실기에서 유지됐다는 기록 이후의 **현재 상태는 UNVERIFIED**다.

## 다음 원인 분리

소프트웨어는 올바른 주소의 수신값을 이미 그대로 읽는다. 이제 같은 서보를 제조사 도구와 별도 제어보드/전원 경로로 읽어, 이상값이 서보를 따라가는지 확인해야 한다. 모터 쪽에서도 재현되면 센서/펌웨어/전원 관련 제조사 점검 대상이고, 특정 어댑터/배선에서만 재현되면 그 경로를 분리한다. 이 물리 교차시험과 제조사 답변은 NOT_RUN이다. 서보 교체·분해·펌웨어 업데이트를 자동으로 하지 않는다.

## 파일 정리

[정리 후보표](FILE_CLEANUP_CANDIDATES.md)에 캐시와 PDF 중간 이미지 약7.53MiB만 사용자 삭제 검토 대상으로 제시했다. 모델·데이터·보정·백업·중단 증거는 유지다. 현재 실행기가 tmp 안에 있으므로 tmp 전체 삭제는 안 된다. 최상위 `START_HERE_SO101.md`로 현재 파일 진입점을 정리했다. 이동·삭제는 하지 않았다.
