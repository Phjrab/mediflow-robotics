"""Freeze an episode split, then materialize local LeRobot views with train-only statistics."""
import argparse
import hashlib
import json
import os
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

COLORS = ("red", "green", "blue")
SEED = 101


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def freeze(manifest):
    rows = json.loads(manifest.read_bytes())["train"]
    ids = [r["merged_episode_index"] for r in rows]
    assert sorted(ids) == list(range(137))
    assert len({(r["source"], r["source_episode_index"]) for r in rows}) == 137
    assert sum(r["frames"] for r in rows) == 63551
    rng = random.Random(SEED)
    held = set()
    for color in COLORS:
        candidates = sorted(r["merged_episode_index"] for r in rows if r["color"] == color)
        held.update(rng.sample(candidates, 7))
    spans = defaultdict(list)
    episodes = []
    for r in sorted(rows, key=lambda x: x["merged_episode_index"]):
        i = r["merged_episode_index"]
        fold = "eval" if i in held else "train"
        episodes.append(dict(id=i, source=r["source"], source_id=r["source_episode_index"],
                             color=r["color"], frames=r["frames"], fold=fold))
        for view in r["views"]:
            start = view["decoded_start_index"]
            spans[view["sha256"]].append((start, start + r["frames"], i))
    for intervals in spans.values():
        ordered = sorted(intervals)
        if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping raw camera frame ranges")
    return dict(seed=SEED, manifest_sha256=digest(manifest), fps=30, episodes=episodes)


def numeric_stats(arr):
    import numpy as np
    result = {key: getattr(np, key)(arr, axis=0).tolist() for key in ("min", "max", "mean", "std")}
    for q in (1, 10, 50, 90, 99):
        result[f"q{q:02}"] = np.percentile(arr, q, axis=0).tolist()
    result["count"] = [len(arr)]
    return result


def materialize(source, split, dest, onehot):
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    deriv = json.loads((source / "DERIVATION.json").read_bytes())
    if deriv["manifest_sha256"] != split["manifest_sha256"]:
        raise ValueError("Different source manifest")
    for name, sha in deriv["metadata_sha256"].items():
        if digest(source / name) != sha:
            raise ValueError(f"Source digest mismatch: {name}")
    datafile = "data/chunk-000/file-000.parquet"
    epfile = "meta/episodes/chunk-000/file-000.parquet"
    all_rows = pq.read_table(source / datafile)
    metadata = {r["episode_index"]: r for r in pq.read_table(source / epfile).to_pylist()}
    stats = {}
    for fold in ("train", "eval"):
        target = dest / fold
        info = json.loads((source / "meta/info.json").read_bytes())
        if onehot:
            state = info["features"]["observation.state"]
            state.update(shape=[9], names=state["names"] + list(COLORS))
        pieces, records, offset = [], [], 0
        for e in (x for x in split["episodes"] if x["fold"] == fold):
            m = metadata[e["id"]].copy()
            n = e["frames"]
            t = all_rows.slice(m["dataset_from_index"], n)
            assert t["episode_index"].unique().to_pylist() == [e["id"]]
            assert t["task_index"].unique().to_pylist() == [COLORS.index(e["color"])]
            assert m["length"] == n and m["tasks"] == [f"Put medicine bottle A into the {e['color']} basket"]
            changes = {"index": range(offset, offset+n), "episode_index": [len(records)] * n}
            for k, v in changes.items():
                t = t.set_column(t.schema.get_field_index(k), k, pa.array(v, type=t.schema.field(k).type))
            if onehot:
                task = [float(e["color"] == c) for c in COLORS]
                k = "observation.state"
                t = t.set_column(t.schema.get_field_index(k), k, pa.array(
                    [s + task for s in t[k].to_pylist()], type=pa.list_(pa.float32(), 9)))
            m.update(episode_index=len(records), dataset_from_index=offset, dataset_to_index=offset+n)
            pieces.append(t)
            records.append(m)
            offset += n
        table = pa.concat_tables(pieces)
        if fold == "train":
            stats = {k: numeric_stats(np.asarray(table[k].to_pylist(), dtype=np.float64))
                     for k in ("action", "observation.state")}
        info.update(total_episodes=len(records), total_frames=offset, splits={"train": f"0:{len(records)}"})
        for file, obj in ((datafile, table), (epfile, pa.Table.from_pylist(records))):
            (target / file).parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(obj, target / file)
        write(target / "meta/info.json", info)
        write(target / "meta/stats.json", stats)
        shutil.copyfile(source / "meta/tasks.parquet", target / "meta/tasks.parquet")
    for asset in deriv["video_assets"]:
        rel = Path(asset["destination"])
        src = source / rel
        if digest(src) != asset["sha256"]:
            raise ValueError(f"Video digest mismatch: {rel}")
        for fold in ("train", "eval"):
            dst = dest / fold / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    write(dest / "split.json", split)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("split", type=Path)
    p.add_argument("--source", type=Path)
    p.add_argument("--dest", type=Path)
    p.add_argument("--onehot", action="store_true")
    args = p.parse_args()
    split = freeze(args.manifest)
    if args.split.exists() and json.loads(args.split.read_bytes()) != split:
        raise ValueError("Existing split differs")
    if not args.split.exists():
        write(args.split, split)
    if args.source:
        if not args.dest or args.dest.exists():
            raise ValueError("Provide a new destination")
        args.dest.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.dest.parent) as tmp:
            payload = Path(tmp) / "dataset"
            materialize(args.source, split, payload, args.onehot)
            payload.rename(args.dest)
    for fold in ("train", "eval"):
        rows = [r for r in split["episodes"] if r["fold"] == fold]
        print(fold, len(rows), sum(r["frames"] for r in rows), dict(Counter(r["color"] for r in rows)))


if __name__ == "__main__":
    main()
