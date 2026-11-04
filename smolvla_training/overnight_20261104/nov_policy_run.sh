#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "${1:-dry}" in
  dry) ;;
  *) echo 'REFUSED: 이 무인 프로파일은 dry만 허용'; exit 2 ;;
esac
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd -- "$task_root"
"$HOME/venvs/smolvla-py310/bin/python" -B -c 'import nov_readonly as n; n.check_stop()'
bash "$task_root/nov_preflight.sh"
timeout --signal=INT --kill-after=5s 180s "$HOME/venvs/smolvla-py310/bin/python" -B "$task_root/dry_inference.py"
