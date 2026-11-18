#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/so101_vla"
export PYTHONPATH=live:live/deps:tools:sim:analysis
py="$HOME/venvs/smolvla-py310/bin/python"
exec {lease}>"${XDG_RUNTIME_DIR:-/tmp}/so101-vla-live.lock"
flock -n "$lease" || { echo 'Another VLA session owns the lease'; exit 2; }
"$py" - <<'PY'
import json
from urllib.request import urlopen
for name in ('teleoperation', 'recording', 'inference'):
    with urlopen('http://127.0.0.1:8022/' + name + '-status', timeout=5) as response:
        status = json.load(response)
    if status.get(name + '_active') is not False:
        raise SystemExit('Active/unknown LeLab activity: ' + name)
PY
lelab=$(systemctl --user is-active lelab-jetson-py310.service || true)
restore() {
    local code=$?
    trap - EXIT
    if [[ $lelab == active ]]; then systemctl --user start lelab-jetson-py310.service || code=1; fi
    exit "$code"
}
trap restore EXIT
systemctl --user stop lelab-jetson-py310.service
out="reports/20261118/RAW_BOUNDARY_RECOVERY/PING_$(date +%s).json"
echo "PING only, register writes=0. Output: $out"
"$py" -B tools/servo_ping_readonly.py --execute --out "$out"
