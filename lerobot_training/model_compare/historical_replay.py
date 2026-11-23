"""Replay the historical deployment checkpoint on archived, already-seen episodes."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

import numpy as np
import pyarrow.parquet as pq
import torch

from metrics import FPS, closing, placement, release
from prepare import COLORS, write
from replay_protocol import HISTORICAL, HISTORICAL_SHA, HORIZON, digest


def samples(split, archive):
    with archive.open(encoding="utf-8", newline="") as stream:
        mapping = {int(r["merged_episode_index"]): r for r in csv.DictReader(stream)}
    for row in split["episodes"]:
        key = row["id"]
        original = mapping[key]
        if (Path(original["source_repo_id"]).name, int(original["source_episode_index"]), original["color"]) != (row["source"], row["source_id"], row["color"]):
            raise ValueError(f"Archive mapping differs for episode {key}")
        yield row, original["source_repo_id"]


def cached_episode(cache, source, row, metadata, tables):
    root = cache/source
    if source not in metadata:
        info = json.loads((root/"meta/info.json").read_bytes())
        if info["fps"] != FPS:
            raise ValueError(f"Archive FPS mismatch for {source}")
        entries = {ep["episode_index"]: ep for p in (root/"meta/episodes").rglob("*.parquet")
                   for ep in pq.ParquetFile(p).read().to_pylist()}
        metadata[source] = (info, entries)
    info, episodes = metadata[source]
    source_id = row["source_id"]
    ep = episodes[source_id]
    path = root/info["data_path"].format(chunk_index=ep["data/chunk_index"], file_index=ep["data/file_index"])
    if path not in tables:
        table = {}
        for frame in pq.ParquetFile(path).read(columns=["episode_index", "frame_index", "timestamp",
                                                        "observation.state", "action"]).to_pylist():
            table.setdefault(frame["episode_index"], []).append(frame)
        tables[path] = table
    frames = sorted(tables[path][source_id], key=lambda frame: frame["frame_index"])[:row["frames"]]
    if [f["frame_index"] for f in frames] != list(range(row["frames"])):
        raise ValueError(f"Archive frame indices differ for episode {row['id']}")
    task = f"Put medicine bottle A into the {row['color']} basket"
    if ep["tasks"] != [task]:
        raise ValueError(f"Archive task differs for episode {row['id']}")
    return root, info, ep, frames


def run(args):
    repo = args.archive_repo
    sys.path[:0] = [str(repo/part) for part in ("analysis", "live", "live/deps", "tools", "sim")]
    os.environ.update(SO101_CONFIG=str(repo/"live/config.yaml"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      TOKENIZERS_PARALLELISM="false")
    from process_policy import RealEngine, deny_hardware

    sys.addaudithook(deny_hardware)
    from checkpoint_amp import infer_with_saved_amp
    from config_runtime import contract, load
    from eval_blue import frames_for
    from observation_contract import pack_observation

    if digest(args.checkpoint/"model.safetensors") != HISTORICAL_SHA:
        raise ValueError("Historical checkpoint digest mismatch")
    split = json.loads(args.split.read_bytes())
    indexed = {}
    metadata, tables = {}, {}
    for row, source in samples(split, args.mapping):
        indexed[row["id"]] = (row, cached_episode(args.cache, source, row, metadata, tables))
    train = [(row["color"], np.asarray([f["action"] for f in ep[3]]))
             for row, ep in indexed.values() if row["fold"] == "train"]
    scale = np.maximum(np.concatenate([a[:, :5] for _, a in train]).std(0), 1e-6)
    refs = []
    for color, actions in train:
        segment = placement(actions, release(actions, closing(actions)), scale)
        if segment is not None:
            refs.append((color, segment))
    if {color for color, _ in refs} != set(COLORS):
        raise ValueError("Missing placement reference color")
    cfg = json.loads((args.checkpoint/"config.json").read_bytes())
    cfg_runtime = contract(load())
    engine = RealEngine({"checkpoint": str(args.checkpoint)})
    if args.latency:
        from replay_latency import benchmark

        recorded_samples = []
        for row, ep in indexed.values():
            if row["fold"] != "eval":
                continue
            root, info, meta, frames = ep
            frame = closing(np.asarray([f["action"] for f in frames]))
            frame = frame if frame is not None else len(frames)//2
            rgb = frames_for(root, info, meta, frames, [frame])[frame]
            recorded_samples.append((rgb, frames[frame]["observation.state"], row["color"]))

        def infer_recorded(sample):
            rgb, state, color = sample
            task = f"Put medicine bottle A into the {color} basket"
            batch = engine.pre(pack_observation(rgb, state, task, torch, cfg_runtime))
            prediction = infer_with_saved_amp(lambda: engine.policy.predict_action_chunk(batch), torch, cfg)
            return engine.post(prediction)[0].float().cpu().numpy(), None

        engine.policy.reset()
        torch.manual_seed(101)
        record = benchmark(infer_recorded, recorded_samples, model=HISTORICAL, mode="chunk",
                           weight_sha=HISTORICAL_SHA, device="cuda", warmup=args.warmup,
                           count=args.iterations, expected_length=cfg["chunk_size"])
        record.update(historical=True, heldout_for_this_model=False, split_sha256=digest(args.split))
        write(args.output, record)
        return
    results = []
    with torch.inference_mode():
        for key, (row, ep) in indexed.items():
            if row["fold"] != "eval":
                continue
            root, info, meta, frames = ep
            actions = np.asarray([f["action"] for f in frames])
            event = release(actions, closing(actions))
            windows = {}
            if event is not None and event >= FPS:
                offsets = list(range((event-FPS)//HORIZON*HORIZON, event, HORIZON))
                images = frames_for(root, info, meta, frames, offsets)
                for color in COLORS:
                    chunks = []
                    for frame in offsets:
                        task = f"Put medicine bottle A into the {color} basket"
                        packed = pack_observation(images[frame], frames[frame]["observation.state"],
                                                  task, torch, cfg_runtime)
                        generator = torch.Generator(device="cuda").manual_seed(101+100000*key+frame)
                        noise = torch.randn((1, cfg["chunk_size"], cfg["max_action_dim"]),
                                            generator=generator, device="cuda")
                        engine.policy.reset()
                        processed = engine.pre(packed)
                        model = lambda: engine.policy.predict_action_chunk(processed, noise=noise)
                        raw = infer_with_saved_amp(model, torch, cfg)
                        chunk = engine.post(raw)[0].float().cpu().numpy()
                        if not np.isfinite(chunk).all() or chunk.shape != (cfg["chunk_size"], 6):
                            raise ValueError(f"Invalid output for episode {key}")
                        lo, hi = max(0, event-FPS-frame), min(HORIZON, event-frame)
                        chunks.append(chunk[lo:hi])
                    windows[color] = np.concatenate(chunks)
                    if windows[color].shape != (FPS, 6):
                        raise ValueError(f"Incomplete release window for episode {key}")
            recorded = row["color"]
            for requested in COLORS:
                if requested == recorded:
                    continue
                nearest, change = None, None
                if windows:
                    segment = windows[requested][:, :5]/scale
                    nearest = min(refs, key=lambda ref: np.square(ref[1]-segment).mean())[0]
                    change = float(np.abs(windows[requested][:, :5]-windows[recorded][:, :5]).mean())
                results.append(dict(episode=key, recorded=recorded, requested=requested, nearest=nearest,
                                    arm_change_mae_deg=change))
            print(f"Episode {key}: {len(results)}/42 swaps", flush=True)
    if len(results) != 42:
        raise ValueError("Expected 42 swapped prompts")
    write(args.output, dict(model=HISTORICAL, weight_sha256=HISTORICAL_SHA,
          split_sha256=digest(args.split), historical=True, heldout_for_this_model=False,
          open_loop_horizon=HORIZON, eligible=sum(r["nearest"] is not None for r in results),
          n=len(results), matches=sum(r["requested"] == r["nearest"] for r in results), rows=results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive_repo", "cache", "mapping", "split", "checkpoint", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--latency", action="store_true")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=200)
    run(parser.parse_args())
