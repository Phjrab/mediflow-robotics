"""기존 v6 모듈을 재사용한다. 설치본/원본 코드를 수정하지 않는다."""
import hashlib
import json
import os
from pathlib import Path
import sys

def text_sha(path):
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()

def prepare():
    manifest=json.loads((Path(__file__).parent/"dependencies.json").read_text(encoding="utf-8"))
    candidates=[Path(os.environ["V6_PREP_ROOT"]).expanduser()] if os.environ.get("V6_PREP_ROOT") else [
        Path(__file__).parent.parent/"dry_rearm_20261105",
        Path.home()/"v6_dry_rearm_worker_20261105_1793878117328"]
    for root in candidates:
        if not root.is_dir():continue
        if any(not (root/name).is_file() or text_sha(root/name)!=digest for name,digest in manifest.items()):
            raise RuntimeError("기존 v6 의존 코드 해시 불일치; 자동 대체/설치 없음")
        sys.path.insert(0,str(root));return root
    raise RuntimeError("재사용할 v6 작업 폴더를 찾지 못함")
