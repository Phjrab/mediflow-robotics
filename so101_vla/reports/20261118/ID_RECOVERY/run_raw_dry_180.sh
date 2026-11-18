#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/so101_vla"
export SO101_CONFIG="$HOME/so101_vla/live/config.yaml"
export PYTHONPATH=live:live/deps:tools:sim:analysis
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
py="$HOME/venvs/smolvla-py310/bin/python"
exec {lease}>"${XDG_RUNTIME_DIR:-/tmp}/so101-vla-live.lock"
flock -n "$lease" || { echo 'Another VLA session owns the lease'; exit 2; }
"$py" - <<'PY'
import json
from urllib.request import urlopen
for name in ('teleoperation','recording','inference'):
    with urlopen('http://127.0.0.1:8022/'+name+'-status',timeout=5) as response:
        status=json.load(response)
    if status.get(name+'_active') is not False:
        raise SystemExit('Active or unknown LeLab activity: '+name)
PY
systemctl is-active --quiet astra-v4l2-bridge.service
for service in medicine-yolo-preview.service so101-camera-preview.service; do
    if systemctl is-active --quiet "$service"; then echo "Camera owner active: $service"; exit 2; fi
done
lelab=$(systemctl --user is-active lelab-jetson-py310.service || true)
alignment=$(systemctl --user is-active so101-camera-alignment.service || true)
restore() {
    local code=$?
    trap - EXIT
    if [[ $lelab == active ]]; then systemctl --user start lelab-jetson-py310.service || code=1; fi
    if [[ $alignment == active ]]; then systemctl --user start so101-camera-alignment.service || code=1; fi
    exit "$code"
}
trap restore EXIT
systemctl --user stop so101-camera-alignment.service lelab-jetson-py310.service
# entry.py installs a process-wide audit hook denying all real servo serial opens.
"$py" -B live/entry.py --mode raw --dry-run --duration 180 --speed 0.33
