"""File-only Jetson latency probe for a trained compact policy. No camera/robot imports."""
import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn


def measure(path, samples):
    bundle = torch.load(path, map_location="cpu", weights_only=True)
    weights = bundle["weights"]
    dim = bundle["dim"]
    policy = nn.Sequential(nn.Linear(dim, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU(), nn.Linear(128, 96))
    policy.load_state_dict(weights, strict=True)
    expected = bundle["model"]
    x = torch.tensor(samples[expected])
    if x.shape[1] != dim:
        raise ValueError("Invalid feature size")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    policy.cuda().eval()
    mean, std, ym, ys = [bundle[k].cuda() for k in ("xmean", "xs", "ym", "ys")]
    times = []
    with torch.inference_mode():
        for i in range(220):
            row = x[i % len(x):i % len(x)+1]
            torch.cuda.synchronize()
            start = time.perf_counter()
            result = (policy((row.cuda()-mean)/std).reshape(-1,6)*ys + ym).cpu()
            torch.cuda.synchronize()
            dt = time.perf_counter()-start
            assert result.shape == (16, 6) and torch.isfinite(result).all()
            if i >= 20:
                times.append(dt*1000)
    return dict(model=expected, platform=platform.machine(), gpu=torch.cuda.get_device_name(),
                torch=str(torch.__version__), weights_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                observations=len(x), chunks=len(times), warmup=20, raw_ms=times, chunk_actions=16,
                mean_ms=float(np.mean(times)), p95_ms=float(np.percentile(times, 95)),
                actions_per_s=16000/float(np.mean(times)), peak_alloc_mib=torch.cuda.max_memory_allocated()/2**20,
                peak_reserved_mib=torch.cuda.max_memory_reserved()/2**20)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    samples = np.load(args.directory/"observations.npz", allow_pickle=False)
    order = ("rgb_mlp", "rgb_mlp_task", "rgb_mlp_task", "rgb_mlp")
    records = [measure(args.directory/(m+".pt"), samples) for m in order]
    out = []
    for m in dict.fromkeys(order):
        group = [r for r in records if r["model"] == m]
        times = [t for r in group for t in r["raw_ms"]]
        mean = float(np.mean(times))
        out.append(dict(group[0], raw_ms=times, order=order, block_means_ms=[r["mean_ms"] for r in group],
                        chunks=len(times), warmup=sum(r["warmup"] for r in group),
                        mean_ms=mean, p95_ms=float(np.percentile(times,95)), actions_per_s=16000/mean,
                        peak_alloc_mib=max(r["peak_alloc_mib"] for r in group),
                        peak_reserved_mib=max(r["peak_reserved_mib"] for r in group)))
    args.output.write_text(json.dumps(out, indent=2)+"\n", encoding="utf-8")
    for record in out:
        print(record["model"], record["mean_ms"], record["p95_ms"], record["peak_alloc_mib"])
