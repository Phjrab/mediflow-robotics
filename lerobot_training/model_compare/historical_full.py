"""Replay the incumbent deployment on the entire training-overlapped evaluation fold.

Run on the Jetson with the archived deployment environment. Supply the deployment
repository, local LeRobot cache, deployed pretrained_model, frozen folds directory,
original episode mapping CSV, and a separate output directory, in that order.
--inventory-only verifies the source actions, videos, split, and checkpoint without
initializing CUDA. Full replay uses saved camera frames and blocks hardware access.
"""
import argparse
import gc
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pyarrow.parquet as pq

from historical_replay import cached_episode, samples
from metrics import load_fold, score
from prepare import write
from replay_protocol import digest, HISTORICAL, HISTORICAL_SHA, HORIZON, RAW_VIEWS

SPLIT_SHA = "9c97855b7abf137a5ca50d9af291b7f06556b6cd4b262f71f5bbdda7b8a13ff1"


def archived_episodes(folds, mapping, cache):
    split = json.loads((folds / "split.json").read_bytes())
    truth = load_fold(folds, split, "eval")
    recorded = pq.ParquetFile(folds / "eval/data/chunk-000/file-000.parquet").read(
        columns=["episode_index", "frame_index", "timestamp", "observation.state"]
    )
    observation_rows = {}
    for frame in recorded.to_pylist():
        observation_rows.setdefault(frame["episode_index"], []).append(frame)
    metadata, tables, indexed = {}, {}, []
    for row, source in samples(split, mapping):
        if row["fold"] != "eval":
            continue
        episode = cached_episode(cache, source, row, metadata, tables)
        root, info, meta, frames = episode
        color, actions = truth[row["id"]]
        if row["color"] != color or not np.array_equal(actions, [f["action"] for f in frames]):
            raise ValueError(f"Archive actions or label differ for episode {row['id']}")
        folded = observation_rows[len(indexed)]
        for field in ("frame_index", "timestamp", "observation.state"):
            if not np.array_equal([f[field] for f in frames], [f[field] for f in folded]):
                raise ValueError(f"Archive {field} differs for episode {row['id']}")
        for camera in RAW_VIEWS:
            key = "observation.images." + camera
            prefix = "videos/" + key + "/"
            video = root / info["video_path"].format(
                video_key=key,
                chunk_index=meta[prefix + "chunk_index"],
                file_index=meta[prefix + "file_index"],
            )
            if not video.is_file():
                raise FileNotFoundError(video)
        indexed.append((row, episode))
    if len(indexed) != 21 or sum(row["frames"] for row, _ in indexed) != 9842:
        raise ValueError("Expected the frozen 21 episodes and 9,842 frames")
    return indexed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive_repo", "cache", "checkpoint", "folds", "mapping", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    repo = args.archive_repo.resolve()
    sys.path[:0] = [str(repo / part) for part in ("analysis", "live", "live/deps", "tools", "sim")]
    os.environ.update(
        SO101_CONFIG=str(repo / "live/config.yaml"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    from process_policy import RealEngine, deny_hardware

    sys.addaudithook(deny_hardware)
    import torch
    from checkpoint_amp import infer_with_saved_amp
    from config_runtime import contract, load
    from eval_blue import frames_for
    from observation_contract import pack_observation

    checkpoint = args.checkpoint.resolve()
    weights = checkpoint / "model.safetensors"
    if digest(weights) != HISTORICAL_SHA or digest(args.folds / "split.json") != SPLIT_SHA:
        raise ValueError("Historical weight or evaluation split digest mismatch")
    indexed = archived_episodes(args.folds, args.mapping, args.cache)
    cfg = json.loads((checkpoint / "config.json").read_bytes())
    provenance = dict(
        model=HISTORICAL,
        historical=True,
        heldout_for_this_model=False,
        weight_sha256=HISTORICAL_SHA,
        split_sha256=SPLIT_SHA,
        mapping_sha256=digest(args.mapping),
        torch=torch.__version__,
        transformers=version("transformers"),
        use_amp=cfg["use_amp"],
        chunk_size=cfg["chunk_size"],
        denoising_steps=cfg["num_steps"],
        open_loop_horizon=HORIZON,
        seed="101+100000*episode+frame",
        expected_episode_count=len(indexed),
        frames=sum(row["frames"] for row, _ in indexed),
        windows=sum((row["frames"] + HORIZON - 1) // HORIZON for row, _ in indexed),
    )
    if args.inventory_only:
        print(json.dumps(provenance), flush=True)
        return

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob("episode_*.npy")) or (output / "gpu_replay_provenance.json").exists():
        raise FileExistsError("Use a separate output directory for each complete replay")
    cfg_runtime = contract(load())
    engine = RealEngine({"checkpoint": str(checkpoint)})
    provenance.update(device=torch.cuda.get_device_name(0))
    write(output / "gpu_replay_provenance.json", provenance)
    start = time.monotonic()
    count = 0
    with torch.inference_mode():
        for row, (root, info, meta, frames) in indexed:
            key = row["id"]
            task = f"Put medicine bottle A into the {row['color']} basket"
            offsets = list(range(0, len(frames), HORIZON))
            rgb = frames_for(root, info, meta, frames, offsets)
            chunks = []
            for offset in offsets:
                engine.policy.reset()
                generator = torch.Generator(device="cuda").manual_seed(101 + 100000 * key + offset)
                noise = torch.randn((1, cfg["chunk_size"], cfg["max_action_dim"]), generator=generator, device="cuda")
                packed = pack_observation(rgb.pop(offset), frames[offset]["observation.state"], task, torch, cfg_runtime)
                processed = engine.pre(packed)
                raw = infer_with_saved_amp(lambda: engine.policy.predict_action_chunk(processed, noise=noise), torch, cfg)
                chunk = engine.post(raw)[0].float().cpu().numpy()
                if chunk.shape != (cfg["chunk_size"], 6) or not np.isfinite(chunk).all():
                    raise ValueError(f"Invalid output for episode {key}, frame {offset}")
                chunks.append(chunk[: min(HORIZON, len(frames) - offset)].copy())
                count += 1
            prediction = np.concatenate(chunks)
            if prediction.shape != (row["frames"], 6):
                raise ValueError(f"Incomplete trajectory for episode {key}")
            np.save(output / f"episode_{key}.npy", prediction, allow_pickle=False)
            print(f"episode {key}, {count}/{provenance['windows']} windows, {time.monotonic()-start:.1f}s", flush=True)
            del rgb
            gc.collect()

    if count != provenance["windows"] or digest(weights) != HISTORICAL_SHA:
        raise ValueError("Final window count or checkpoint digest mismatch")
    provenance.update(
        windows_complete=count,
        seconds=time.monotonic() - start,
        checkpoint_sha_rechecked=True,
        amp_dtype=str(torch.get_autocast_dtype("cuda")),
        peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
        peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved(),
    )
    for filename, color in (("metrics.json", None), ("metrics_blue.json", "blue")):
        write(output / filename, {**provenance, **score(args.folds, output, color)})
    write(output / "gpu_replay_provenance.json", provenance)


if __name__ == "__main__":
    main()
