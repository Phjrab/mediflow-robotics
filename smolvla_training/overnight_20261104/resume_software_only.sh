#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$task_root"
task_python="$HOME/venvs/smolvla-py310/bin/python"
test -x "$task_python" || { echo 'BLOCKED: 지정 SmolVLA 환경 없음. 설치/다른 환경 변경 없음'; exit 2; }
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
task_stamp=$(date +%Y%m%d_%H%M%S)_$$
task_reports="$task_root/reports/software_resume_$task_stamp"
mkdir -- "$task_reports"
set -o noclobber
"$task_python" -B run_software_checks.py >"$task_reports/tests.log" 2>&1
echo 'SOFTWARE_TESTS_PASS: 장치/모델 실행 없는 관련 테스트'
"$task_python" -B software_preflight.py --out "$task_reports/preflight.json"
task_status=0
if timeout --signal=INT --kill-after=10s 240s "$task_python" -B benchmark_offline_precision.py --run-offline --out "$task_reports/precision" >"$task_reports/precision.log" 2>&1; then
  echo 'OFFLINE_PRECISION_FINISHED: SUMMARY.json의 모드별 성공/오류 확인 필요'
else
  task_status=1
  echo 'OFFLINE_PRECISION_FAILED: 원인은 precision.log, 제어/학습 재시도 없음'
fi
if "$task_python" -B cached_action_replay.py --out "$task_reports/cached_replay" >"$task_reports/cached_replay.log" 2>&1; then
  echo 'CACHED_REPLAY_FINISHED: 실제 예측 재사용, 현장 승인/상태는 만들어 넣지 않음'
else
  task_status=1
  echo 'CACHED_REPLAY_FAILED: cached_replay.log 확인'
fi
echo "REPORT_DIRECTORY=reports/software_resume_$task_stamp"
echo 'ROBOT_SERIAL_CAMERA_LIVE: NOT_RUN. 중단 기록 유지, 실제 제어 준비 완료를 뜻하지 않음'
exit "$task_status"
