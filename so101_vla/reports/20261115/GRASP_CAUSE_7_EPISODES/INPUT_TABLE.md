# 학습·실행 입력 대조

이번 검증은 원본137회 매핑에서 균등하게 고른 blue5회, red1회, green1회다. source episode/frame과 비디오 시각을 보존해 AV1을 RGB로 디코딩했다. 기존 준비 스크립트 `prepare_three_colors_exclude10.py`는 비디오 바이트와 action/state를 그대로 복사하고 매핑·해시를 검증한다. 학습에 없던 이미지 재인코딩이나 궤적 수정은 없다.

| 항목 | 학습/저장 설정 | 실제 실행 경로 | 대조 |
|---|---|---|---|
| blue task | `Put medicine bottle A into the blue basket` | 같은 UTF-8 문자열 | 에피소드 tasks와 글자 단위 동일 |
| red/green task | 같은 문장의 red/green | 같은 문자열 | 동일 |
| 손목 카메라 | `observation.images.end_effector` | wrist USB RGB | 저장 rename에서 camera1 |
| 수직 카메라 | `observation.images.ceiling_vertical` | Astra RGB loopback | camera2 |
| 사선 카메라 | `observation.images.ceiling_oblique` | RealSense RGB serial236223023645/interface03 | camera3; 실제 구도는 달라짐 |
| 이미지 원본 | 640×480 RGB | 640×480 RGB uint8 | 동일 |
| 초기 이미지 변환 | float32 BCHW /255 | 같은 변환 | 공식 경로와 텐서 대조 |
| 모델 이미지 처리 | resize_with_pad512×512, 이후[-1,1] | 저장 모델 메서드 그대로 | 동일 |
| feature 선언 크기 | config에는3×256×256 | 학습 config도 동일 | 별도256 강제 리사이즈 없음 |
| 이미지 정규화 | VISUAL=IDENTITY, ImageNet stats 사용false | 동일 | 임의 RGB 표준화 없음 |
| state 단위 | 앞5축degree, gripper0~100 | LeRobot bus normalize 출력 같은 단위 | tick을 모델에 직접 넣지 않음 |
| state 정규화 | 체크포인트의 MEAN_STD | 저장 preprocessor safetensors | 실시간 통계로 재계산하지 않음 |
| action 역정규화 | 체크포인트의 MEAN_STD | 저장 postprocessor safetensors | chunk 전체/개별 후처리 대조 |
| chunk_size/n_action_steps | 50/50 | 50/50 | 동일 |
| n_obs_steps | 1 | 1 | 동일 |
| 디노이징 | num_steps10 | 10 | 동일 |
| VLM 레이어 | 16 | 16 | 동일 |
| AMP | use_amp=true | CUDA autocast(float16) | 저장 설정 유지 |
| RTC | 학습null | 비활성 | 동일 |

정규화 파일별 SHA256과 수치는 `normalization_stats.json`, 설정 비교는 `full_frame_evaluation/input_contract.json`에 저장한다. 가중치·정규화 파일·학습 데이터는 수정하지 않았다.

공식 경로는 설치된 LeRobot0.6.0의 `lerobot.policies.utils.prepare_observation_for_inference`와 저장 pre/postprocessor를 사용한다. 실행기는 `pack_observation`과 같은 저장 processor를 사용한다. 공식 함수의 일반 소스는 [Hugging Face LeRobot](https://github.com/huggingface/lerobot/blob/main/src/lerobot/policies/utils.py)에서도 확인할 수 있다. 검증에는 인터넷 최신판 대신 Jetson 설치본을 사용했다.

모든 프레임에서 두 경로의 전처리 텐서를 대조하고 동일 입력의 추론은 공유했다. 50액션 모두의 후처리는 별도로 비교했고, 각 에피소드 시작·닫힘·끝에서 공식 경로를 다시 추론했다. 고정 잡음으로 입력 차이와 확률적 예측 차이를 분리했다. 오프라인은 batch2이며, batch1 대비 수치 민감도는 별도 benchmark에 보존했다(대표 프레임 최대0.181°/점). 실제 live는 batch1을 유지한다.

녹화 action과 예측 사이 오차는 모델의 근사 오차도 포함한다. 공식·실행기 결과가 같다면 그 오차만으로 입력 처리 버그라고 판정할 수 없다. 데이터에 들어 있는 정답 action을 정책 출력 대신 사용해 오프라인 평가를 맞추지는 않는다. 녹화 action 재생은 별도 물리 시험이며 모델 평가와 분리한다.
