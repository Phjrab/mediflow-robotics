# MediFlow VLM 준비 결과

작성일: 2026-09-22

## 완료된 작업

- `CODEX_HANDOFF.md`와 `VLM_PROMPT.md` 전체 확인
- 데이터셋 archive SHA-256 검증
- 원본 1,226 이미지 및 1,226 JSONL 레코드 무결성 검증
- 원본을 수정하지 않는 `dataset_v2` manifest 생성
- `before_grasp` 450개만 선택
- 정답 클래스가 포함된 원래 instruction 제거
- 카메라 전환 및 30초 gap 기반 32개 후보 session 구성
- session 단위 train/validation/test 분리
- Qwen3-VL-2B 추론, QLoRA 학습, JSON 평가 도구 구현
- 격리 Python 환경과 Qwen3-VL-2B 모델 snapshot 다운로드
- CPU zero-shot 30개 baseline 실행
- RTX 3060에서 PyTorch CUDA와 4-bit bitsandbytes 추론 확인
- 보수적으로 필터링한 파일럿 manifest와 선택 보고서 생성
- 1 epoch 및 3 epoch QLoRA 파일럿 학습·평가 완료
- 원본과 분리된 로컬 label 검수 UI 구현 및 실제 브라우저 검증

## dataset_v2

| Split | 이미지 | Session |
|---|---:|---:|
| train | 278 | 23 |
| validation | 88 | 5 |
| test | 84 | 4 |

Session 교차 누수는 없습니다. 450개 모두 `review_status: pending`이며 perceptual near-duplicate 후보는 275개입니다.

파이프라인 검증용 `pilot_*` manifest는 원래 split과 label을 유지하면서 근접 중복 후보와 `tilted/unknown` 자세만 제외했습니다.

| Pilot split | 이미지 | A/B/C | upright/fallen |
|---|---:|---:|---:|
| train | 109 | 38/38/33 | 35/74 |
| validation | 27 | 25/0/2 | 13/14 |
| test | 34 | 14/2/18 | 20/14 |

파일럿 label도 아직 `pending`입니다. 이번 학습은 코드·GPU·방향성 검증을 위한 명시적 override이며 최종 데이터 승인으로 간주하지 않습니다.

## Zero-shot baseline

Qwen/Qwen3-VL-2B-Instruct로 test 앞부분 30장을 CPU 추론했습니다.

- JSON schema 유효: 30/30
- medicine accuracy: 46.67%
- orientation accuracy: 53.33%
- target-bin accuracy: 46.67%
- exact JSON match: 0%
- wrong-bin rate: 53.33%

모델은 30개를 모두 `A/upright`로 예측했습니다. A/B/C가 프로젝트 내부의 임의 클래스이므로 zero-shot만으로는 시각적 매핑을 학습할 수 없다는 것이 확인됐습니다.

## GPU 및 QLoRA 파일럿 결과

호스트에서 RTX 3060(VRAM 11.63 GiB), NVIDIA driver 595.84, PyTorch 2.11.0+cu130 및 `torch.cuda.is_available() == True`를 확인했습니다. Qwen3-VL-2B 4-bit GPU 추론과 QLoRA 학습 모두 성공했습니다.

동일한 격리 test 34장 결과:

| 모델 | exact match | medicine | orientation | target bin | wrong bin |
|---|---:|---:|---:|---:|---:|
| zero-shot | 2.94% | 41.18% | 58.82% | 41.18% | 58.82% |
| QLoRA 1 epoch | 2.94% | 41.18% | 50.00% | 41.18% | 58.82% |
| QLoRA 3 epoch | 20.59% | 41.18% | 55.88% | 41.18% | 58.82% |

3 epoch validation loss는 5.435 → 4.918 → 4.885로 감소했고 자세 분류를 일부 학습했지만, medicine/target-bin 성능은 개선되지 않았습니다. 특히 C 18개 중 16개를 A로 예측했습니다. 이 어댑터는 파이프라인 검증 결과물이며 로봇 실동작에 사용하면 안 됩니다.

현재 차단 조건은 CUDA가 아니라 데이터입니다.

1. 450개 label의 사람 검수가 완료되지 않았습니다.
2. validation/test 클래스 구성이 불균형하고 test의 B가 2개뿐입니다.
3. 카메라·배경·세션과 medicine class 사이 상관관계를 줄이는 재수집 또는 split 재설계가 필요합니다.
4. QLoRA는 미검수 데이터와 CPU 학습을 기본적으로 계속 거부합니다.

## Label 검수 UI

로컬 전용 검수 서버를 `http://127.0.0.1:8020/`에서 실행할 수 있습니다.

```bash
work/venvs/mediflow-vlm/bin/mediflow-review
```

- 전체 450개 진행률과 상태 확인
- 기본값으로 근접 중복 후보를 숨겨 우선 검수 대상 175개 표시
- A/B/C/unknown 및 upright/fallen/tilted/unknown 수정
- 그리퍼 접근/파지 부위를 `lid`(뚜껑), `body`(몸통), `unknown`으로 별도 검수
- 승인, 제외, 대기 복원, 메모 및 키보드 단축키 지원
- 결정 이력을 `dataset_v2/review_decisions.jsonl`에 append-only 저장
- 승인본만 `dataset_v2/reviewed_{train,validation,test}.jsonl`로 별도 export
- 원본 `data/pilot`, `dataset_v2/manifest.jsonl`, 카메라 및 로봇 설정은 수정하지 않음
- 2026-09-23 Jetson 신규 촬영 494장을 `data/imported/jetson_20260923`으로 분리 반입
- 기존 450장과 합친 `review_manifest.jsonl`에서 `신규 촬영` 필터로 검수 가능

## 기존 캡처 저장소

Jetson의 `/home/USER/so101_vlm_capture`를 `so101_vlm_capture/`로 가져왔습니다.

- 카메라 설정 보존: wrist `/dev/video6`, Astra/RealSense HTTP stream
- 카메라·저장·라벨 검증 코드 확인
- 로봇 제어, calibration, ArUco, Depth 구현은 이 저장소에 없음
- 오래된 테스트 fixture만 현재 라벨 스키마에 맞게 갱신
- 캡처 저장소 테스트 11개 통과
- 검수 저장소/API 포함 VLM 학습 도구 테스트 10개 통과

## 호스트 실행 명령

프로젝트 루트에서:

```bash
work/venvs/mediflow-vlm/bin/mediflow-prepare

work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.make_pilot

work/venvs/mediflow-vlm/bin/mediflow-infer \
  --manifest dataset_v2/test.jsonl \
  --output dataset_v2/predictions-zero-shot.jsonl \
  --load-in-4bit \
  --resume

work/venvs/mediflow-vlm/bin/mediflow-evaluate \
  --manifest dataset_v2/test.jsonl \
  --predictions dataset_v2/predictions-zero-shot.jsonl \
  --output dataset_v2/evaluation-zero-shot.json
```

검수 완료 후 QLoRA:

```bash
work/venvs/mediflow-vlm/bin/mediflow-train \
  --train-manifest dataset_v2/train.jsonl \
  --validation-manifest dataset_v2/validation.jsonl \
  --output work/qwen3-vl-2b-mediflow-lora
```

이번 파일럿 결과:

- `work/qwen3-vl-2b-mediflow-pilot-lora-3epoch/final_adapter/`
- `dataset_v2/predictions-pilot-lora-3epoch.jsonl`
- `dataset_v2/evaluation-pilot-lora-3epoch.json`
- `dataset_v2/pilot_selection_report.json`

GPU 확인:

```bash
nvidia-smi
work/venvs/mediflow-vlm/bin/python -c \
  'import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CUDA unavailable")'
```

로봇 제어, 카메라 초기화, calibration 변경, 원본 데이터 수정/삭제는 수행하지 않았습니다.
