#!/usr/bin/env bash
set -euo pipefail
taskPython="$HOME/venvs/smolvla-py310/bin/python"
taskLibDir="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib"
test -x "$taskPython" || { echo '중단: 전용 Python 없음'; exit 1; }
test -d "$taskLibDir" || { echo '중단: 기존 cuSPARSELt 폴더 없음'; exit 1; }
export LD_LIBRARY_PATH="$taskLibDir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$taskPython" -B "$HOME/v6_process_1793887709179/operator_candidate/servo_config_readonly.py" "$@"
