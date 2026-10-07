"""Archive explicit project assets without modifying data or running hardware."""

import hashlib
import json
from pathlib import Path
import tarfile

from prepare_public_repository import SECRET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/release_assets_20261007"
MODEL_EXT = {".safetensors", ".json", ".md", ".jinja"}


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def pack(name, paths, models=False):
    target = OUT / (name + ".tar.gz")
    sources = []
    for path in paths:
        sources.extend(sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink()))
    sources = [p for p in sources if not models or p.suffix in MODEL_EXT]
    assert sources, name
    for path in sources:
        if path.name.startswith(".env") or path.suffix in {".key", ".pem"}:
            raise ValueError(f"Sensitive file: {path.relative_to(ROOT)}")
        if path.suffix in {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}:
            if SECRET.search(path.read_text()):
                raise ValueError(f"Possible credential: {path.relative_to(ROOT)}")
    if not target.exists():
        partial = target.with_suffix(target.suffix + ".partial")
        with tarfile.open(partial, "w:gz", compresslevel=1) as archive:
            for path in sources:
                archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        partial.rename(target)
    with tarfile.open(target, "r:gz") as archive:
        assert len(archive.getmembers()) == len(sources), name
    result = {"file": target.name, "bytes": target.stat().st_size,
              "sha256": digest(target), "files": len(sources),
              "sources": [str(p.relative_to(ROOT)) for p in paths]}
    assert result["bytes"] < 2 * 1024**3, "Split oversized release asset before upload"
    print(json.dumps(result), flush=True)
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "assets_manifest.json").exists():
        raise RuntimeError("Archives already inventoried; inspect manifest before rebuilding")
    records = []
    original = ROOT / "so101_pilot_dataset_ready.tar.gz"
    if original.exists():
        records.append({"file": original.name, "bytes": original.stat().st_size,
                        "sha256": digest(original), "sources": [original.name]})
    records.append(pack("mediflow-original-capture-data-20261007", [ROOT / "data"]))
    for dataset in sorted((ROOT / "work/lerobot_datasets/Supermassive111").iterdir()):
        if dataset.is_dir():
            name = dataset.name.removeprefix("Supermassive111_")
            records.append(pack("dataset-" + name, [dataset]))
    for run in sorted((ROOT / "work/lerobot_outputs").iterdir()):
        checkpoints = run / "checkpoints"
        if not checkpoints.is_dir():
            continue
        steps = sorted(p for p in checkpoints.iterdir() if p.name.isdigit() and
                       (p / "pretrained_model/model.safetensors").exists())
        if not steps:
            continue
        chosen = steps[-1]
        entry = pack("model-" + run.name + "-step" + chosen.name,
                     [chosen / "pretrained_model"], models=True)
        entry["step"] = int(chosen.name)
        entry["complete_100k"] = int(chosen.name) == 100000
        records.append(entry)
    adapters = sorted((ROOT / "work").glob("qwen3-*/final_adapter"))
    adapters += sorted((ROOT / "work").glob("qwen3-*-v1-20260926/checkpoint-47"))
    records.append(pack("models-vlm-adapters-20261007", adapters, models=True))
    manifest = {"date_kst": "2026-10-07", "repo": "Phjrab/mediflow-robotics",
                "uploaded": False, "assets": records}
    (OUT / "assets_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Prepared {len(records)} assets; upload not performed.", flush=True)


if __name__ == "__main__":
    main()
