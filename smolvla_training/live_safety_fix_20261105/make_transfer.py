"""검토한 코드만 새 ZIP에 담는다. 인증/원본 데이터/가중치 미포함."""
import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
names = ("live_safety_runtime.py", "probe_current_state.py", "so101_first_action_test_safe_v6.py",
         "test_live_safety_fix.py", "source_v4.py", "build_v6.py")
manifest = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
path = root / "live_safety_code.zip"
with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as z:
    for name in names:
        z.write(root/name, name)
    z.writestr("manifest.json", json.dumps(manifest, indent=2))
print(json.dumps({"file":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"code_files":len(names)}))
