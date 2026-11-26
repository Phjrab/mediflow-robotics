"""Create a patched private copy only from the supported installed sources."""
import hashlib
import json
from pathlib import Path
import subprocess
import sysconfig
import tempfile

root = Path(__file__).resolve().parent
site = Path(sysconfig.get_paths()["purelib"])
for name,wanted in json.loads((root/"sources.json").read_bytes()).items():
    if hashlib.sha256((site/name).read_bytes()).hexdigest() != wanted:
        raise SystemExit(f"Unsupported LeLab installation: {name}")
overlay=root/"overlay"
overlay.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=overlay) as tmp:
    for name in ("record", "server"):
        dest = Path(tmp) / f"{name}.py"
        dest.write_bytes((site / "lelab" / dest.name).read_bytes())
        subprocess.run(["patch", "--batch", "--fuzz=0", str(dest), str(root / f"{name}.patch")], check=True)
        compile(dest.read_bytes(), str(dest), "exec")
    for name in ("record.py", "server.py"):
        (Path(tmp) / name).replace(overlay / name)
