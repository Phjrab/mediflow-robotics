#!/usr/bin/env bash
set -euo pipefail
taskRoot=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HOME/venvs/smolvla-py310/bin/python" -B "$taskRoot/servo_home_recovery.py" "$@"
