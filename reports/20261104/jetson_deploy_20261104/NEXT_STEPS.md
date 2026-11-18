# 3색 SmolVLA: 다음 작업 (2026-11-04)

## 확인 완료
첨부 종료 로그 두 개에서 20,000/20,000 및 End of training 확인. 빨강·초록 93회, 최종 3색 137회/63,551프레임. 최종 구성은 빨강48·초록45·파랑44다. 파랑 10번째를 추가 제외한 최신 목록을 따른다. 과거 64회 인계보다 이번 완료 기록을 우선한다.
저장 샘플 재로딩 기록은 3색 모두 [1,6], finite=true다. 실기 성공률은 미측정이다.

## 지금 필요한 것
첨부 ZIP은 코드·기록만 약306KB이며 모델은 없다. 학습 PC에서 최종 pretrained_model 폴더 전체 7개를 받는다. 합계 1197820587 bytes, model.safetensors는 1,197,789,224 bytes다. 정상화 safetensors 두 개도 필수다. 감사용 pretrained_model_configs는 원본 파일 해시와 다를 수 있으므로 모델 폴더에 대신 넣지 않는다.

학습 PC 코덱스에는 COPY_MODEL_PROMPT.md를 전달한다. 새 학습은 필요 없다.
받은 뒤 이 폴더에서 실행(표준 라이브러리만, 장치 접근 없음):

```powershell
python -B ./verify_checkpoint_receipt.py --checkpoint '받은/pretrained_model' --receipt ./checkpoint_files.json
```

7개 모두 PASS가 나와야 한다. 현재 이 검사는 모델 부재로 NOT_RUN이다.

## 다음 진행 순서
1. 모델 전체 받기 → 위 해시 검증.
2. Jetson의 기존 환경에서 설치 소스/API·CUDA 호환 확인. CUDA/torch를 일반 pip로 덮어쓰지 않는다. 필요한 모델/토크나이저 캐시 유무도 확인.
3. 먼저 카메라·로봇 없이 저장 샘플 또는 더미 입력으로 로드/추론 및 지연 확인. 학습 PC의 출력 기록은 Jetson에서 재검증한 것이 아니다.
4. 최신 실행 코드 확보·관절 단위/순서·안전 계층 연결 확인 후 shadow 준비. 첨부 vla_run.py는 plan/mock만 구현되어 shadow/live를 거부한다. --confirm-live 같은 미지원 옵션을 사용하지 않는다. 이 사본으로 live 명령을 제공할 수 없다.
5. 현장 확인과 별도 실행 요청을 받은 뒤 실기 진행. 현재 로봇/serial/카메라 실행 없음.

## 카메라 대응
- 손목 USB / end_effector → observation.images.camera1
- 천장 수직 Astra / ceiling_vertical → observation.images.camera2
- 천장 사선 RealSense / ceiling_oblique → observation.images.camera3
체크포인트 policy_preprocessor.json에 원래 저장 키→모델 키 변환이 이미 포함되어 있다. 실행기에서 중복 변환하지 않는다. 입력은 정상화 전 값, 출력은 postprocessor의 역정규화 경로를 확인해야 한다. 단위는 기록 설정 근거로 확인하고 값 범위로 추정하지 않는다.
Astra 색 불일치는 알려진 상태이며 녹화 때와 같은 브리지 경로를 유지한다.

## 상태
수행: ZIP 별도 압축 해제, 종료/샘플검사 증거 대조, 수신 검증 도구 준비.
NOT_RUN: 모델 수신·실물 해시 검사, Jetson 접속/설치/추론, shadow/live, 재학습, push.
재개: 모델 폴더 수신 후 위 verify 명령. 롤백: 원본 변경 없음; 이 준비 폴더만 추가됨.
