#!/usr/bin/env bash
# Default DRY RUN: no device/GPU/system probing. --probe is for a later onsite run.
set -euo pipefail
task_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "${VLA_PYTHON:-python3}" -B "$task_script_dir/jetson_preflight.py" "$@"
