# 파일 정리 검토표 — 2026-11-11

요청 범위는 목록 조사와 사용자 판단용 제안이다. 삭제·이동·이름변경·SSH·하드웨어 접근을 하지 않았다. 이번 작업으로 작성한 파일은 이 보고서 하나다. 숫자는 파일 논리 크기의 합이며 디스크 실제 사용량과 다를 수 있고, 다른 담당의 작업으로 이후 바뀔 수 있다.

## 용량 현황

일반 파일 합계 약 2050.98 MiB(약 2.00 GiB), 별도로 루트 `.git` 약 1479.95 MiB와 중첩 Git 메타데이터 약 12.94 MiB가 있다. 아래 폴더 집계는 `.git`을 제외한다. Git 저장소/워크트리 관계를 손상할 수 있으므로 `.git` 삭제나 prune/gc는 제안하지 않는다.

| 폴더 | 대략 크기 | 파일 수 |
|---|---:|---:|
| [.local](C:/Users/User/Documents/ChatGPT/자율설계/.local) | 1962.86 MiB | 3067 |
| [reports](C:/Users/User/Documents/ChatGPT/자율설계/reports) | 79.42 MiB | 2576 |
| [output](C:/Users/User/Documents/ChatGPT/자율설계/output) | 4.98 MiB | 6 |
| [tmp](C:/Users/User/Documents/ChatGPT/자율설계/tmp) | 3.71 MiB | 100 |
| [vla_prep_local](C:/Users/User/Documents/ChatGPT/자율설계/vla_prep_local) | 0.01 MiB | 3 |
| [SO101_CODEX_RESTART_PROMPT.md](C:/Users/User/Documents/ChatGPT/자율설계/SO101_CODEX_RESTART_PROMPT.md) | 0.00 MiB | 1 |

## 사용자 삭제 검토 후보

현재 확인한 소규모 재생성 파일만 후보로 올린다. 아래 두 범주의 합은 약 7.53 MiB이며, 큰 데이터나 체크포인트를 지워야 확보할 수 있는 용량과 혼동하면 안 된다. 실제 삭제 여부는 사용자가 결정한다.

| 경로 | 대략 크기 | 용도 | 권장 | 조건·근거 |
|---|---:|---|---|---|
| [.local/recording-inspect-deps/pyarrow/tests/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/tests/__pycache__) | 3.057 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/PIL/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/PIL/__pycache__) | 1.332 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/tests/parquet/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/tests/parquet/__pycache__) | 0.354 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/__pycache__) | 0.258 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/video/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/video/__pycache__) | 0.164 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/__pycache__) | 0.156 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/container/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/container/__pycache__) | 0.103 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/codec/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/codec/__pycache__) | 0.099 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/parquet/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/parquet/__pycache__) | 0.092 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/interchange/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/interchange/__pycache__) | 0.051 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/vendored/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/vendored/__pycache__) | 0.050 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/audio/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/audio/__pycache__) | 0.048 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-video-fallback/imageio_ffmpeg/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-video-fallback/imageio_ffmpeg/__pycache__) | 0.043 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/pyarrow/tests/interchange/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/pyarrow/tests/interchange/__pycache__) | 0.042 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/filter/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/filter/__pycache__) | 0.037 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/subtitles/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/subtitles/__pycache__) | 0.027 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av/sidedata/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av/sidedata/__pycache__) | 0.027 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [tmp/vla_direct_live_v3_20261109/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/tmp/vla_direct_live_v3_20261109/__pycache__) | 0.023 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [reports/v6_process_isolation_20261105/operator_candidate/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/reports/v6_process_isolation_20261105/operator_candidate/__pycache__) | 0.013 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [reports/pypi_connectivity_20261104/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/reports/pypi_connectivity_20261104/__pycache__) | 0.007 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [reports/overnight_20261104/package/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/reports/overnight_20261104/package/__pycache__) | 0.007 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-inspect-deps/av-19.0.2.dist-info/licenses/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps/av-19.0.2.dist-info/licenses/__pycache__) | 0.006 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [.local/recording-video-fallback/imageio_ffmpeg/binaries/__pycache__](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-video-fallback/imageio_ffmpeg/binaries/__pycache__) | 0.000 MiB | Python 바이트코드 캐시 | 사용자삭제검토 | 같은 상위 폴더의 .py/.pyd/라이브러리 본체는 유지. Python이 실행 시 캐시를 재생성한다. |
| [tmp/pdfs/robot-60.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-60.png) | 0.055 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-61.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-61.png) | 0.198 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-62.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-62.png) | 0.103 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-63.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-63.png) | 0.080 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-64.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-64.png) | 0.285 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-65.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-65.png) | 0.271 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-66.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-66.png) | 0.086 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |
| [tmp/pdfs/robot-contact.png](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/robot-contact.png) | 0.449 MiB | PDF 작성·렌더링 확인용 중간 이미지 | 사용자삭제검토 | 최종 output/pdf 문서 확인 후 선택. build_history*.py와 QA JSON은 유지. 원래 로봇 캡처 사진을 다시 구할 수 있는지는 확인하지 못했다. |

PDF 이미지 중 로봇 참고 캡처를 재활용할 계획이면 보관으로 바꾼다. 최종 PDF만으로 같은 PNG의 완전 재생성이 보장되지는 않는다.

## 보관 권장: 배포 압축본·복사본·로컬 의존성

이름이 tmp/복사본이어도 실행 근거나 롤백본일 수 있다. 아래 항목은 자동 삭제 후보가 아니다.

| 경로 | 대략 크기 | 용도 | 권장 | 조건·근거 |
|---|---:|---|---|---|
| [.local/recording-inspect-deps](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-inspect-deps) | 171.948 MiB | 녹화 분석용 av/PIL/pyarrow 의존성 | 보관 | audit_three_colors.py, decode_trial_recording.py, inspect_blue_update_20261103.py, inspect_trial_recording.py가 sys.path로 직접 참조. 단순 캐시가 아니며 즉시 삭제 비권장. |
| [.local/recording-video-fallback](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording-video-fallback) | 83.664 MiB | imageio_ffmpeg 및 약 83.58 MiB FFmpeg 실행파일 | 보관 | 미디어 해독 대체 도구. 삭제 후 재설치 가능성은 있지만 동일 버전 복원/분석 재검증을 하지 않았음. |
| [.local/mediflow-publish](C:/Users/User/Documents/ChatGPT/자율설계/.local/mediflow-publish) | 10.739 MiB | 게시·인계용 저장소 복사본 | 보관 | stage_smolvla_handoff.py, export_handoff_parts.py가 직접 참조. 원본 동일성/미커밋 변경 확인 전 삭제 금지. |
| [.local/mediflow-blue-publish](C:/Users/User/Documents/ChatGPT/자율설계/.local/mediflow-blue-publish) | 10.776 MiB | blue 게시용 저장소 복사본 | 보관 | stage_blue_publish.py가 직접 참조. 원본 동일성/미커밋 변경 확인 전 삭제 금지. |
| [.local/astra-color-work](C:/Users/User/Documents/ChatGPT/자율설계/.local/astra-color-work) | 4.503 MiB | Astra 작업용 Git 워크트리 | 보관 | 내부 .git은 82바이트 파일이다. 독립 복사본으로 간주하지 말 것. |
| [reports/overnight_20261104](C:/Users/User/Documents/ChatGPT/자율설계/reports/overnight_20261104) | 27.779 MiB | 과거 배치 실행 기록 및 repo/package 복사본 | 보관 | 중단 관측 serial_stop_observation.json 포함. 과거 근거/복원용으로 보관. |
| [tmp/data_motion_20261109.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/data_motion_20261109.zip) | 0.037 MiB | 이전 배포/전송 묶음 | 보관 | 동일 폴더에서 모든 원본을 대응 확인할 수 없었음. 유일 복사본/복원 근거 가능성이 있어 삭제 추천 제외. |
| [tmp/lelab_guard_20261109.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/lelab_guard_20261109.zip) | 0.007 MiB | 이전 배포/전송 묶음 | 보관 | 동일 폴더에서 모든 원본을 대응 확인할 수 없었음. 유일 복사본/복원 근거 가능성이 있어 삭제 추천 제외. |
| [tmp/servo_b_once_20261109/servo-b.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/servo_b_once_20261109/servo-b.zip) | 0.005 MiB | 이전 배포/전송 묶음 | 보관 | 내부 2개 파일이 같은 폴더 원본과 바이트 동일. 배포 이력 묶음이므로 보관 권장. |
| [tmp/servo_b_step_20261109/servo-step.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/servo_b_step_20261109/servo-step.zip) | 0.005 MiB | 이전 배포/전송 묶음 | 보관 | run.py는 현재 폴더 파일과 다르고 run.sh는 같은 폴더에 없음. 중복 아님. |
| [tmp/servo_b_step_20261109/stability-dashboard.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/servo_b_step_20261109/stability-dashboard.zip) | 0.013 MiB | 이전 배포/전송 묶음 | 보관 | 내부 4개 파일이 같은 폴더 파일과 바이트 동일. 이전 서비스 화면/설치 스냅샷 보관. |
| [tmp/servo_home_recovery_20261109.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/servo_home_recovery_20261109.zip) | 0.013 MiB | 이전 배포/전송 묶음 | 보관 | 동일 폴더에서 모든 원본을 대응 확인할 수 없었음. 유일 복사본/복원 근거 가능성이 있어 삭제 추천 제외. |
| [tmp/teleop_hold_20261109/direct_follow/direct-follow.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/teleop_hold_20261109/direct_follow/direct-follow.zip) | 0.006 MiB | 이전 배포/전송 묶음 | 보관 | 내부 2개 파일이 같은 폴더 파일과 바이트 동일. 과거 버전 보관. |
| [tmp/teleop_hold_20261109/legacy_follow/rollback-follow.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/teleop_hold_20261109/legacy_follow/rollback-follow.zip) | 0.009 MiB | 이전 배포/전송 묶음 | 보관 | 내부 3개 파일이 동일하지만 이름과 용도가 rollback이다. 삭제 추천 제외. |
| [tmp/teleop_hold_20261109/payload.zip](C:/Users/User/Documents/ChatGPT/자율설계/tmp/teleop_hold_20261109/payload.zip) | 0.011 MiB | 이전 배포/전송 묶음 | 보관 | 내부 4개 파일이 같은 폴더 파일과 바이트 동일. 과거 HOLD 배포 근거 보관. |
| [tmp/vla_auto_validation_20261110.tar.gz](C:/Users/User/Documents/ChatGPT/자율설계/tmp/vla_auto_validation_20261110.tar.gz) | 0.037 MiB | 이전 배포/전송 묶음 | 보관 | final 묶음과 4개 멤버 내용이 다름: monitor_web/screen.js, run.sh, startup_simulation.py, stream_live.py. 중복 아님. |
| [tmp/vla_auto_validation_final_20261110.tar.gz](C:/Users/User/Documents/ChatGPT/자율설계/tmp/vla_auto_validation_final_20261110.tar.gz) | 0.037 MiB | 이전 배포/전송 묶음 | 보관 | 앞선 묶음과 SHA-256 및 위 4개 내용이 다름. 이름만 보고 다른 파일 삭제 금지. |

## 유지 대상

| 경로 | 대략 크기 | 용도 | 권장 | 근거 |
|---|---:|---|---|---|
| [.local/models](C:/Users/User/Documents/ChatGPT/자율설계/.local/models) | 1142.333 MiB | 학습 체크포인트/설정 | 유지 | model.safetensors 단일 파일 약 1142.30 MiB. 이번 요청에서 체크포인트 삭제/변경 제외. |
| [.local/three_color_checks_20261102_204203](C:/Users/User/Documents/ChatGPT/자율설계/.local/three_color_checks_20261102_204203) | 465.683 MiB | 3색 영상 데이터·검증 원본 | 유지 | 녹화 원본/데이터로 취급. 다른 저장소 동일성은 확인하지 않았음. |
| [.local/recording_checks](C:/Users/User/Documents/ChatGPT/자율설계/.local/recording_checks) | 69.222 MiB | 실제 녹화 검사 영상/기록 | 유지 | trial/main 영상과 데이터 증거. |
| [.local/completed_handoff_20261104](C:/Users/User/Documents/ChatGPT/자율설계/.local/completed_handoff_20261104) | 0.916 MiB | 완료 인계 소스·학습 근거 | 유지 | 학습 종료/체크포인트 목록/설정 인계 근거. |
| [.local/recovered_sources](C:/Users/User/Documents/ChatGPT/자율설계/.local/recovered_sources) | 0.141 MiB | 복구한 소스 기록 | 유지 | 복구 원본 및 변경 근거. |
| [tmp/vla_direct_live_v3_20261109](C:/Users/User/Documents/ChatGPT/자율설계/tmp/vla_direct_live_v3_20261109) | 0.327 MiB | 현 실행기·thermal·시작조건 모듈 및 테스트 | 유지 | tmp 이름이어도 최신 실행 코드. 폴더 일괄 삭제 금지. |
| [reports/team_20261111](C:/Users/User/Documents/ChatGPT/자율설계/reports/team_20261111) | 0.348 MiB | 이번 UI/실행기/평가/통합 검증 | 유지 | 배포 기준과 운영 인계 유지. |
| [reports/thermal_deep_20261111](C:/Users/User/Documents/ChatGPT/자율설계/reports/thermal_deep_20261111) | 10.230 MiB | 온도 조사·중단 판단 근거 | 유지 | 중단 로그와 진단 근거 유지. |
| [reports/work_session_hold_20261108](C:/Users/User/Documents/ChatGPT/자율설계/reports/work_session_hold_20261108) | 1.611 MiB | HOLD 세션·raw 관측 | 유지 | 0동작/중단 판단 등 증거로 보존. |
| [reports](C:/Users/User/Documents/ChatGPT/자율설계/reports) | 79.419 MiB | 전체 검증 보고서·번들·중단 관측 | 유지 | .bundle/.tar.gz도 근거·복원 가능성이 있으므로 일괄 정리 금지. |
| [output](C:/Users/User/Documents/ChatGPT/자율설계/output) | 4.978 MiB | 최종 PDF·진단·비교 결과 | 유지 | 사용자에게 전달된 최종 산출물. |
| [tmp/pdfs/build_history.py](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/build_history.py) | 0.040 MiB | PDF 생성 소스 | 유지 | 문서 재현에 필요. |
| [tmp/pdfs/build_history_integrated.py](C:/Users/User/Documents/ChatGPT/자율설계/tmp/pdfs/build_history_integrated.py) | 0.024 MiB | 통합 PDF 생성 소스 | 유지 | 문서 재현에 필요. |
| [vla_prep_local](C:/Users/User/Documents/ChatGPT/자율설계/vla_prep_local) | 0.007 MiB | 비교 계약·모델 메타데이터 | 유지 | 실행 전 검증 조건 근거. |
| [SO101_CODEX_RESTART_PROMPT.md](C:/Users/User/Documents/ChatGPT/자율설계/SO101_CODEX_RESTART_PROMPT.md) | 0.005 MiB | 재시작/인계 지침 | 유지 | 다음 담당의 맥락 유지. |

추가 유지: `.bak_` 포함 파일 189개 약 1.62 MiB. 기존 수정 전 백업·롤백본이므로 삭제 추천하지 않는다. calibration/home/보정 원본과 `stop`, `hold`, `before`, `raw` 증거도 보존한다. 이름 검색만으로 원본/사본 여부가 확정되는 것은 아니다.

## 조사 방법과 한계

`rg --files --hidden`으로 목록을 먼저 확인하고, 읽기 전용 os.walk/stat로 크기를 합산했다. zip/tar 멤버와 일부 같은 폴더 파일을 메모리에서 바이트 비교했고 압축 해제·실행은 하지 않았다. 압축파일명/폴더명만으로 중복이라 판단하지 않았다. Git 메타데이터 내용/로그나 데이터 영상·모델은 실행하지 않았다. 저장소 상태·외부/Jetson의 다른 복사본 유무·재설치 가능성은 검증하지 않았다. 따라서 대용량 의존성이나 저장소 복사본의 실제 삭제 여부는 후속 확인 뒤 사용자 결정이 필요하다.
