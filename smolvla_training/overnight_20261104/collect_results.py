"""완료된 보고서와 대표 이미지만 새 묶음에 보존한다. 장치를 열지 않는다."""
import hashlib
import tarfile
import time
from pathlib import Path

root = Path(__file__).resolve().parent
out = Path.home() / ('overnight_results_%s.tar.gz' % time.strftime('%Y%m%d_%H%M%S'))
paths = []
for folder in ('offline_eval_v2', 'offline_samples', 'cameras_current'):
    for path in (root / 'reports' / folder).iterdir():
        if path.is_file() and '.bak_' not in path.name and path.suffix in ('.csv', '.json', '.md', '.png'):
            paths.append(path)
for relative in ('reports/INVENTORY.json', 'reports/REFUSAL_CHECKS.json', 'SERIAL_STOP.json',
                 'reports/FINAL_SYSTEM_STATUS.json', 'reports/BACKUPS.csv'):
    path = root / relative
    if path.exists():
        paths.append(path)
with tarfile.open(out, 'x:gz') as archive:
    for path in paths:
        archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
print('RESULT_TAR', out.name, len(paths), out.stat().st_size, hashlib.sha256(out.read_bytes()).hexdigest())
