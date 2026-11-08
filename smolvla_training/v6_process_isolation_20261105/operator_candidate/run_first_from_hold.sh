#!/usr/bin/env bash
set -euo pipefail
taskRoot=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$HOME/venvs/smolvla-py310/bin/python" -B "$taskRoot/first_trial_from_hold.py" "$@"
