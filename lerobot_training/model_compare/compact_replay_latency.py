"""Measure the compact controls from the three recorded raw RGB frames on Jetson."""
import argparse
import json
from pathlib import Path
import sys
import time

import av
import numpy as np
import torch

from compact import HEIGHT, HORIZON, VIEWS, WIDTH, net
from historical_replay import cached_episode, samples
from metrics import closing
from prepare import COLORS, write
from replay_latency import add_measurement_arguments, benchmark
from replay_protocol import digest


def run(args):
    repo = args.archive_repo
    sys.path[:0] = [str(repo/part) for part in ("analysis", "live", "live/deps", "tools", "sim")]
    from process_policy import deny_hardware

    sys.addaudithook(deny_hardware)
    from eval_blue import frames_for

    split = json.loads(args.split.read_bytes())
    if not torch.cuda.is_available():
        raise ValueError("An accessible CUDA Jetson is required for this benchmark")
    samples_rgb = []
    metadata, tables = {}, {}
    for row, source in samples(split, args.mapping):
        if row["fold"] != "eval":
            continue
        root, info, episode, frames = cached_episode(args.cache, source, row, metadata, tables)
        frame = closing(np.asarray([entry["action"] for entry in frames]))
        frame = frame if frame is not None else len(frames)//2
        rgb = frames_for(root, info, episode, frames, [frame])[frame]
        samples_rgb.append((rgb, np.asarray(frames[frame]["observation.state"], dtype=np.float32), row["color"]))
    if len(samples_rgb) != 21:
        raise ValueError("Expected all 21 archived evaluation episodes")

    torch.set_num_threads(2)
    order = ("rgb_mlp", "rgb_mlp_task", "rgb_mlp_task", "rgb_mlp")
    records = []
    for model in order:
        path = args.weights/(model+".pt")
        weights = torch.load(path, map_location="cpu", weights_only=True)
        if weights["model"] != model:
            raise ValueError(f"Unexpected model in {path.name}")
        policy = net(weights["dim"]).eval()
        policy.load_state_dict(weights["weights"], strict=True)
        policy.cuda()
        xmean, xs, ym, ys = [weights[k].cuda() for k in ("xmean", "xs", "ym", "ys")]

        def call(sample):
            rgb, state, color = sample
            begin = time.perf_counter()
            flat = []
            for view in VIEWS:
                frame = av.VideoFrame.from_ndarray(rgb[view], format="rgb24")
                pixels = frame.reformat(width=WIDTH, height=HEIGHT, interpolation="AREA").to_ndarray()
                flat.append(pixels.reshape(-1).astype(np.float32)/255)
            flat.append(state)
            if model == "rgb_mlp_task":
                flat.append(np.asarray([float(c == color) for c in COLORS], dtype=np.float32))
            x = torch.from_numpy(np.concatenate(flat))
            rgb_ms = (time.perf_counter()-begin)*1000
            result = (policy((x.cuda()-xmean)/xs).reshape(HORIZON,6)*ys+ym).cpu().numpy()
            return result, rgb_ms

        record = benchmark(call, samples_rgb, model=model, mode="chunk", weight_sha=digest(path),
                           device="cuda", warmup=args.warmup, count=args.iterations, expected_length=HORIZON)
        records.append(record)
        print(model, record["mean_ms"], record["p95_ms"], flush=True)
        del policy, xmean, xs, ym, ys
        torch.cuda.empty_cache()

    result = []
    for model in dict.fromkeys(order):
        blocks = [record for record in records if record["model"] == model]
        raw_ms = [t for record in blocks for t in record["raw_ms"]]
        rgb_ms = [t for record in blocks for t in record["cpu_rgb_preprocess_ms"]]
        mean = float(np.mean(raw_ms))
        result.append(dict(blocks[0], order=order, block_means_ms=[record["mean_ms"] for record in blocks],
                           raw_ms=raw_ms, cpu_rgb_preprocess_ms=rgb_ms, cpu_rgb_preprocess_mean_ms=float(np.mean(rgb_ms)),
                           iterations=len(raw_ms), warmup=len(blocks)*args.warmup, mean_ms=mean,
                           p50_ms=float(np.percentile(raw_ms,50)), p95_ms=float(np.percentile(raw_ms,95)),
                           actions_per_s=HORIZON*1000/mean,
                           peak_alloc_mib=max(record["peak_alloc_mib"] for record in blocks),
                           peak_reserved_mib=max(record["peak_reserved_mib"] for record in blocks),
                           split_sha256=digest(args.split)))
    write(args.output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive_repo", "cache", "mapping", "split", "weights", "output"):
        parser.add_argument(name,type=Path)
    add_measurement_arguments(parser)
    run(parser.parse_args())
