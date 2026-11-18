#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/so101_vla"
export PYTHONPATH=live:live/deps:tools:sim:analysis
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
py="$HOME/venvs/smolvla-py310/bin/python"
"$py" - <<'PY'
import json
from urllib.request import urlopen
for name in ('teleoperation','recording','inference'):
    with urlopen('http://127.0.0.1:8022/'+name+'-status',timeout=5) as response:
        status=json.load(response)
    if status.get(name+'_active') is not False:
        raise SystemExit('Refusing to stop active/unknown LeLab activity: '+name)
PY
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
"$py" - <<'PY'
from pathlib import Path
from PIL import Image
root=Path.home()/'so101_vla'
source=root/'runs/DRY_1795000997722352677'
dest=root/'reports/20261118/ID_RECOVERY/bus_frames'
dest.mkdir(exist_ok=True)
for name in ('end_effector','ceiling_vertical','ceiling_oblique'):
    frame=Image.open(source/('live_'+name+'.png')).convert('RGB')
    assert frame.size==(640,480),frame.size
    frame.save(dest/(name+'.jpg'),quality=95)
PY
out="reports/20261118/ID_RECOVERY/BUS_AB_$(date +%s)"
echo "$out"
"$py" -B tools/recorder_bus_readonly.py --out "$out" --frames reports/20261118/ID_RECOVERY/bus_frames --duration 60
