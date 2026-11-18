#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/so101_vla"
export PYTHONPATH=live:live/deps:tools:sim:analysis
py="$HOME/venvs/smolvla-py310/bin/python"
exec {lease}>"${XDG_RUNTIME_DIR:-/tmp}/so101-vla-live.lock"
echo '다른 검증이 실행 중이면 종료될 때까지 최대 240초 기다립니다.'
flock -w 240 "$lease" || { echo '다른 검증이 아직 실행 중입니다. 모터 PING 미실행.'; exit 2; }
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
# Run only the audited PING worker with sudo. Its nested owner check then does
# not depend on a user's tty/parent-process sudo timestamp being inherited.
sudo env PYTHONPATH="$PYTHONPATH" "$py" -B tools/servo_ping_readonly.py --execute --out "$out"
