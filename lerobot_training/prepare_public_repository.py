"""Export a reviewed public copy; never modify source data or run robot code."""

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "outputs/public_repository_20261007"
ROOTS = ("so101_vlm_capture", "vlm_training", "lerobot_training", "openclaw_aruco", "dataset_v2", "dataset_v3", "docs", "outputs")
TEXT_EXTENSIONS = {".py", ".sh", ".md", ".txt", ".toml", ".json", ".jsonl", ".yaml", ".yml", ".html", ".css", ".js", ".service", ".conf", ".modules", ".urdf", ".csv"}
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".git", ".agents", ".aws", ".codex", "public_repository_20261007", "release_assets_20261007"}
SECRET = re.compile(r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
PRIVATE_IP = re.compile(r"\b(?:192\.168\.(?:\d{1,3}\.)\d{1,3}|10\.(?:\d{1,3}\.){2}\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b")


def selected(path):
    if any(part in SKIP_PARTS or part.endswith(".egg-info") for part in path.parts):
        return False
    if path.is_symlink() or path.name.startswith(".env") or path.suffix in {".bak", ".log", ".stop"}:
        return False
    relative = path.relative_to(ROOT)
    if relative.parts[0] == "outputs" and path.suffix == ".jsonl":
        return False  # raw joint/device streams are not public experiment summaries
    if path.suffix == ".png":
        return "training_reports" in path.parts or path.name == "joint_commands_first25s.png"
    return path.suffix in TEXT_EXTENSIONS or path.name in {".gitignore", ".gitattributes"}


def sanitize(text, relative):
    if SECRET.search(text):
        raise ValueError(f"Possible credential in {relative}; do not publish")
    # Public placeholder copies only; original addresses/paths remain unchanged.
    if str(relative) != "docs/SITES_AND_REPOSITORIES.md":
        text = PRIVATE_IP.sub("127.0.0.1", text)
    text = text.replace("robot@", "robot@").replace("user@", "user@")
    text = re.sub(r"/home/(?:chosun|jetson3)(?=/|\b)", "/home/USER", text)
    if relative.suffix == ".md":
        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
                      lambda m: f"[현장 이미지: 로컬 보관 — {m.group(1)}]" if (
                          re.search(r"\.(?:jpg|jpeg)(?:$|[?#])", m.group(2), re.I)
                          or "ceiling_vertical_first" in m.group(2)
                          or "ceiling_oblique_first" in m.group(2)
                      ) else m.group(0), text)
    return text


def prepare(refresh=False):
    if (STAGE / "manifest.json").exists() and not refresh:
        raise RuntimeError("A public export already exists; inspect it rather than silently overwrite")
    paths = [ROOT / name for name in ("README.md", "VLM_PROMPT.md", ".gitignore", ".gitattributes", "PROJECT_STATUS.md")]
    for folder in ROOTS:
        paths.extend(p for p in (ROOT/folder).rglob("*") if p.is_file())
    manifest, excluded = [], []
    for source in sorted(set(paths)):
        relative = source.relative_to(ROOT)
        if not selected(source):
            excluded.append(str(relative))
            continue
        target = STAGE / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".png":
            shutil.copyfile(source, target)
        else:
            text = sanitize(source.read_text(), relative)
            target.write_text(text)
        data = target.read_bytes()
        if source.suffix == ".py":
            compile(data.decode(), str(relative), "exec")
        if source.suffix == ".json":
            json.loads(data)
        elif source.suffix == ".jsonl":
            for line in data.decode().splitlines():
                if line.strip(): json.loads(line)
        manifest.append({"path": str(relative), "mode": "100755" if source.suffix == ".sh" else "100644",
                         "binary": source.suffix == ".png", "bytes": len(data),
                         "sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()})
    (STAGE/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (STAGE/"excluded.json").write_text(json.dumps(excluded, ensure_ascii=False, indent=2))
    print(json.dumps({"files": len(manifest), "bytes": sum(x["bytes"] for x in manifest),
                      "by_root": dict(Counter(x["path"].split("/")[0] for x in manifest)),
                      "excluded": len(excluded), "stage": str(STAGE)}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "refresh", "plan", "batch", "chunk", "verify"))
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--path")
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    if args.command in {"prepare", "refresh"}:
        prepare(refresh=args.command == "refresh")
        return
    manifest = json.loads((STAGE/"manifest.json").read_text())
    if args.command == "verify":
        remote = json.loads(subprocess.check_output([
            "gh", "api", "repos/Phjrab/mediflow-robotics/git/trees/main?recursive=1"
        ], text=True))
        assert not remote.get("truncated"), "Remote tree truncated"
        hashes = {row["path"]: row["sha"] for row in remote["tree"] if row["type"] == "blob"}
        failures = [row["path"] for row in manifest if hashes.get(row["path"]) != row["sha"]]
        print(json.dumps({"files_checked": len(manifest), "mismatches": failures}, ensure_ascii=False))
        raise SystemExit(bool(failures))
    if args.command == "chunk":
        assert args.path in {x["path"] for x in manifest}
        encoded = base64.b64encode((STAGE/"files"/args.path).read_bytes()).decode()
        print(encoded[args.offset:args.offset+24000])
        return
    batches, current, size, large = [], [], 0, []
    for entry in manifest:
        if entry["binary"] or entry["bytes"] > 30000:
            large.append(entry)
            continue
        content=(STAGE/"files"/entry["path"]).read_text()
        element={"path":entry["path"],"mode":entry["mode"],"type":"blob","content":content}
        cost=len(json.dumps(element,ensure_ascii=False).encode())
        if current and size+cost>40000:
            batches.append(current); current=[]; size=0
        current.append(element); size+=cost
    if current: batches.append(current)
    if args.command == "plan":
        print(json.dumps({"batches":len(batches),"large":large,"files":len(manifest)},ensure_ascii=False))
    else:
        print(json.dumps(batches[args.index],ensure_ascii=False))


if __name__ == "__main__":
    main()
