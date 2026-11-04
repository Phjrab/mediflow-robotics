# 오프라인 평가 결과

**학습에 포함된 원본9회차의 재현 오차이며 held-out 성능이나 로봇 성공률이 아니다.** 토크ON 중단 뒤에는 로봇/serial에 재접근하지 않았다. 현재 카메라+현재 관절값 예측과 live는 BLOCKED/NOT_RUN이다.

## 실행 범위

- red/green/blue 각3회차, 회차마다 시작과중간: 총18표본. 각표본의50×6 예측 전체를 같은 시점부터의 녹화 action50개와 대조했다.
- 녹화 영상 시각은 에피소드 video from_timestamp + 상대 data timestamp로 찾고 오차1.1프레임 이하를 확인했다.
- 원본 제외 목록을 적용해 표본을 골랐으며 원본을 수정하지 않았다. 표본 manifest에 원본/에피소드/시각을 기록했다.
- 동일 입력 지연12회와 동일 입력·동일seed101의 task조건 비교2시점을 실행했다.

## 지연과 메모리

- 모델/processor 로딩: 34.902초.
- 첫 회 2.939162초 /12회평균 2.057449초 /첫회제외평균 1.977293초 /최대 2.939162초.
- dtype torch.float32. PyTorch GPU peak 1.193GiB, 프로세스RSS 3.547GiB, 관측시 시스템 가용RAM 1.674GiB.
- Orin은 공유 메모리이므로 GPU계수와RSS를 더해 전체 물리 메모리라고 계산하지 않는다. CUDA외할당/context는 torch peak계수와 다를 수 있다.
- 지연은 저장된 입력을 사용한 전처리→정책→후처리→GPU동기화 시간이며 카메라 획득은 포함하지 않는다. 첫 회는 별도 표시했다.
- Jetson 열 센서 마지막 값: {"cpu-thermal": 51.5, "soc2-thermal": 49.093, "soc0-thermal": 49.5, "gpu-thermal": 50.843, "tj-thermal": 51.5, "soc1-thermal": 50.031}. 모터 Present_Temperature는 읽지 못해 UNVERIFIED이며 이 값으로 대체하지 않는다.

## 관절별 오차

| 색 | 구간 | 관절 | 회차 | MAE | 최대절대오차 | 단위 |
|---|---|---|---:|---:|---:|---|
| blue | middle | elbow_flex | 3 | 6.1545 | 16.3916 | degree |
| blue | middle | gripper | 3 | 1.9449 | 12.1343 | percent_0_100 |
| blue | middle | shoulder_lift | 3 | 5.0168 | 15.8608 | degree |
| blue | middle | shoulder_pan | 3 | 0.8603 | 2.7144 | degree |
| blue | middle | wrist_flex | 3 | 2.1184 | 6.5970 | degree |
| blue | middle | wrist_roll | 3 | 0.8341 | 2.9669 | degree |
| blue | start | elbow_flex | 3 | 3.5086 | 20.2618 | degree |
| blue | start | gripper | 3 | 0.2496 | 0.9935 | percent_0_100 |
| blue | start | shoulder_lift | 3 | 1.0369 | 3.7972 | degree |
| blue | start | shoulder_pan | 3 | 0.5067 | 5.0472 | degree |
| blue | start | wrist_flex | 3 | 1.7913 | 11.5842 | degree |
| blue | start | wrist_roll | 3 | 0.2908 | 2.8144 | degree |
| green | middle | elbow_flex | 3 | 5.2424 | 19.6194 | degree |
| green | middle | gripper | 3 | 1.3235 | 8.1322 | percent_0_100 |
| green | middle | shoulder_lift | 3 | 3.1702 | 9.3374 | degree |
| green | middle | shoulder_pan | 3 | 1.2424 | 4.5505 | degree |
| green | middle | wrist_flex | 3 | 2.4408 | 10.0625 | degree |
| green | middle | wrist_roll | 3 | 0.7927 | 3.5745 | degree |
| green | start | elbow_flex | 3 | 2.9455 | 18.3046 | degree |
| green | start | gripper | 3 | 0.6711 | 2.1456 | percent_0_100 |
| green | start | shoulder_lift | 3 | 0.7306 | 2.5079 | degree |
| green | start | shoulder_pan | 3 | 0.3545 | 2.0650 | degree |
| green | start | wrist_flex | 3 | 1.0252 | 8.4181 | degree |
| green | start | wrist_roll | 3 | 0.2570 | 1.0874 | degree |
| red | middle | elbow_flex | 3 | 7.1008 | 19.9427 | degree |
| red | middle | gripper | 3 | 3.7708 | 16.9857 | percent_0_100 |
| red | middle | shoulder_lift | 3 | 3.9509 | 13.1102 | degree |
| red | middle | shoulder_pan | 3 | 1.1847 | 4.8894 | degree |
| red | middle | wrist_flex | 3 | 5.7177 | 22.9543 | degree |
| red | middle | wrist_roll | 3 | 0.4973 | 1.7645 | degree |
| red | start | elbow_flex | 3 | 2.4018 | 11.9552 | degree |
| red | start | gripper | 3 | 0.3514 | 0.8538 | percent_0_100 |
| red | start | shoulder_lift | 3 | 0.9859 | 2.7597 | degree |
| red | start | shoulder_pan | 3 | 0.6933 | 5.2329 | degree |
| red | start | wrist_flex | 3 | 0.8689 | 8.0919 | degree |
| red | start | wrist_roll | 3 | 0.2374 | 2.2426 | degree |

시작50프레임에는 정지/접근 준비 구간이 들어갈 수 있어 낮은 MAE가 과제 수행 능력을 보증하지 않는다. 중간 구간을 별도로 보고하고, 각회차 전체 결과는 joint_errors.csv/action_chunks.csv에 남겼다.

## 언어 조건 반응

같은 영상·state에서 task문구만 바꾸고 매번 seed101로 재설정했다. red반복은 같은 문구/같은seed의 대조군이다. 수치 차이는 언어조건에 반응한다는 관측일 뿐 올바른 바구니 선택 증거가 아니다.

| 표본 | task쌍 | 몸체평균차이(도) | 몸체최대차이(도) | 그리퍼평균차이(pp) | 그리퍼최대차이(pp) |
|---|---|---:|---:|---:|---:|
| red_e000_middle | green__blue | 6.012310 | 27.797798 | 0.688440 | 2.199329 |
| red_e000_middle | red__blue | 12.432550 | 61.567619 | 1.590280 | 2.834473 |
| red_e000_middle | red__green | 9.508973 | 33.769821 | 1.417707 | 2.061558 |
| red_e000_middle | red__red_repeat | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| red_e000_start | green__blue | 0.474391 | 8.282944 | 0.049786 | 0.130720 |
| red_e000_start | red__blue | 0.351044 | 3.597519 | 0.043436 | 0.129269 |
| red_e000_start | red__green | 0.672116 | 11.523804 | 0.080522 | 0.201845 |
| red_e000_start | red__red_repeat | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## 50step 실행 시간과 제한 계산

-30Hz에서50개목표의 명목 스케줄은50/30=1.667초다.0.5도/step은15도/초이므로20도/초 상한보다 먼저 제한한다. 그리퍼는기존규칙2퍼센트포인트/step이다.
- 동기 모델이 첫회제외평균1.977초 추론하고1.667초 전송한다면 한주기는명목3.644초이며, 실제관측제어속도는NOT_RUN이다.
-clip계산은 측정관절이 직전전송목표를 완벽히 따른다는 가정의 순수 계산이다. 실제지연·부하·접촉·관절한계는 검증하지 않았다. 모든원래목표에 도달했다는 뜻도 아니다.
| 표본 | clip된step/50 | 명목전송시간(초) | 계산상태 |
|---|---:|---:|---|
| red_e000_start | 29 | 1.667 | 가정하 계산 |
| red_e000_middle | 50 | 1.667 | 가정하 계산 |
| red_e002_start | 35 | 1.667 | 가정하 계산 |
| red_e002_middle | 50 | 1.667 | 가정하 계산 |
| red_e003_start | 39 | 1.667 | 가정하 계산 |
| red_e003_middle | 50 | 1.667 | 가정하 계산 |
| green_e000_start | 24 | 1.667 | 가정하 계산 |
| green_e000_middle | 47 | 1.667 | 가정하 계산 |
| green_e001_start | 36 | 1.667 | 가정하 계산 |
| green_e001_middle | 50 | 1.667 | 가정하 계산 |
| green_e002_start | 35 | 1.667 | 가정하 계산 |
| green_e002_middle | 49 | 1.667 | 가정하 계산 |
| blue_e001_start | 31 | 1.667 | 가정하 계산 |
| blue_e001_middle | 50 | 1.667 | 가정하 계산 |
| blue_e002_start | 38 | 1.667 | 가정하 계산 |
| blue_e002_middle | 50 | 1.667 | 가정하 계산 |
| blue_e005_start | 32 | 1.667 | 가정하 계산 |
| blue_e005_middle | 46 | 1.667 | 가정하 계산 |

최초 자동산출 chunk_limits.json에는 NumPy scalar형 입력을 Python float로 변환하지 않아 제한 계산이 거부된 항목이 있었다. 입력 변환을 수정하고 저장 예측CSV로만 다시 계산한 chunk_limits_corrected.json을 최종값으로 사용한다. 모델 추론을 다시 돌리거나 원래 결과를 덮어쓰지 않았다.

## 카메라 비교

camera_comparison.png의 열1/2/3은 빨강/초록/파랑 학습시작 표본, 열4는 현재 영상이다. 행은camera1=손목, camera2=Astra수직, camera3=RealSense사선이다.

| 카메라 | 열/출처 | RGB전체평균(0~255) | 검정비율 |
|---|---|---:|---:|
| end_effector | 1 / red | 138.0607 | 0.0000% |
| end_effector | 2 / green | 130.3369 | 0.0000% |
| end_effector | 3 / blue | 135.7200 | 0.0000% |
| end_effector | 4 / current | 0.9973 | 1.6673% |
| ceiling_vertical | 1 / red | 118.5116 | 1.3105% |
| ceiling_vertical | 2 / green | 116.2848 | 4.9674% |
| ceiling_vertical | 3 / blue | 118.2260 | 4.6175% |
| ceiling_vertical | 4 / current | 0.0036 | 99.9795% |
| ceiling_oblique | 1 / red | 112.3877 | 0.0000% |
| ceiling_oblique | 2 / green | 106.5290 | 0.1292% |
| ceiling_oblique | 3 / blue | 106.3077 | 0.2415% |
| ceiling_oblique | 4 / current | 3.9778 | 0.2266% |

현재 영상은 매우 어두워 약통/바구니/집게와 구도 일치를 신뢰성 있게 확인하기 어렵다. 원인이 조명·노출·가림 중 무엇인지는 UNVERIFIED이다. 설정·조명을 임의로 바꾸거나 Astra 브리지를 재시작하지 않았다. 키 매핑과 해상도는 학습 설정과 일치하지만 장면 품질은 통과로 처리하지 않는다.
