"""Measure RGB-input preprocessing, policy inference and CPU action return on Jetson."""
import argparse
import json
import platform
from pathlib import Path
import time

import numpy as np
import torch

from prepare import write


def benchmark(call, samples, *, model, mode, weight_sha, device, warmup, count,
              cycle=True, expected_length=None):
    if warmup < 0 or count < 1 or not samples or (not cycle and len(samples) < warmup+count):
        raise ValueError("Insufficient recorded samples, warmup or iterations")
    gpu = device == "cuda"
    if gpu:
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    raw_ms, preprocess_ms = [], []
    with torch.inference_mode():
        for index in range(warmup+count):
            sample = samples[index % len(samples)]
            begin = time.perf_counter()
            actions, preprocessing = call(sample)
            if gpu:
                torch.cuda.synchronize()
            elapsed = (time.perf_counter()-begin)*1000
            if (actions.ndim != 2 or actions.shape[1] != 6 or not np.isfinite(actions).all()
                or (expected_length is not None and expected_length != len(actions))):
                raise ValueError("Unexpected action shape or non-finite action")
            expected_length = len(actions)
            if index >= warmup:
                raw_ms.append(elapsed)
                if preprocessing is not None:
                    preprocess_ms.append(preprocessing)
    mean = float(np.mean(raw_ms))
    return dict(model=model, mode=mode, weight_sha256=weight_sha, platform=platform.machine(),
                device=device, gpu=torch.cuda.get_device_name() if gpu else None,
                torch=str(torch.__version__), stage="recorded RGB preprocessing through CPU action return",
                excludes=["camera capture", "video decoding", "robot I/O"], observations=len(samples),
                warmup=warmup, iterations=count, raw_ms=raw_ms, cpu_rgb_preprocess_ms=preprocess_ms,
                mean_ms=mean, p50_ms=float(np.percentile(raw_ms,50)), p95_ms=float(np.percentile(raw_ms,95)),
                delivered_actions=len(actions), actions_per_s=len(actions)*1000/mean,
                peak_alloc_mib=torch.cuda.max_memory_allocated()/2**20 if gpu else None,
                peak_reserved_mib=torch.cuda.max_memory_reserved()/2**20 if gpu else None)


def measure_official(args):
    from evaluate import ReplayPolicy, checkpoint_for, observations
    from metrics import closing, load_fold

    split, checkpoint, step, target = checkpoint_for(args)
    replay = ReplayPolicy(args, checkpoint, args.mode)
    dataset = observations(args.dataset, replay)
    episodes = load_fold(args.dataset, split, "eval")
    samples = []
    offset = 0
    if args.mode == "temporal":
        # A single contiguous episode keeps previous overlapping chunks in the ensemble.
        total = args.iterations + args.warmup
        for color, actions in episodes.values():
            if len(actions) >= total:
                first = closing(actions) or len(actions)//2
                start = min(max(first-total//2,0),len(actions)-total)
                samples = [(dataset[offset+start+i],color) for i in range(total)]
                break
            offset += len(actions)
    else:
        for color, actions in episodes.values():
            frame = closing(actions)
            samples.append((dataset[offset+(frame if frame is not None else len(actions)//2)], color))
            offset += len(actions)

    def call(sample):
        item, color = sample
        return replay(item, color), None

    replay.reset()
    record = benchmark(call, samples, model=args.model, mode=args.mode, weight_sha=replay.weight_sha,
                       device=args.device, warmup=args.warmup, count=args.iterations,
                       cycle=args.mode != "temporal", expected_length=1 if args.mode == "temporal" else None)
    if args.mode == "temporal":
        record.update(temporal_ensemble_coeff=0.01, n_action_steps=1,
                      predicted_chunk_actions=replay.cfg.chunk_size)
    write(args.output, record)


def add_measurement_arguments(parser):
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=200)


if __name__ == "__main__":
    from evaluate import add_policy_arguments

    parser = argparse.ArgumentParser(description=__doc__)
    add_policy_arguments(parser)
    add_measurement_arguments(parser)
    parser.add_argument("--mode", choices=["chunk", "temporal"], default="chunk")
    measure_official(parser.parse_args())
