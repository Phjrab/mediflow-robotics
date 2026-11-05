import ast
import hashlib
import json
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parent
files=sorted(root.glob("*.py"))
for p in files:ast.parse(p.read_text(encoding="utf-8"),feature_version=(3,10))
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
archive=root/"dry_rearm_code.zip"
with zipfile.ZipFile(archive,"x",compression=zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,p.name)
    z.writestr("manifest.json",json.dumps(manifest,indent=2))
print(json.dumps(dict(files=len(files),sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
