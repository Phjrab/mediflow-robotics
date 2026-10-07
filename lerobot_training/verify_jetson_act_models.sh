#!/usr/bin/env bash
set -euo pipefail

runtime_root=/home/USER/venvs/lelab-jetson-py310
cusparselt_lib="$runtime_root/nvidia-deps/cusparselt-cuda12/usr/lib/aarch64-linux-gnu/libcusparseLt/12"
verify_script=/home/USER/so101-medicine-bootstrap/tools/verify_jetson_act_models.py

export LD_LIBRARY_PATH="$cusparselt_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$runtime_root/bin/python" "$verify_script" "$@"
