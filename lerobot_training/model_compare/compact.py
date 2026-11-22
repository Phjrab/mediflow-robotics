"""CPU comparison controls, not ACT/Diffusion. Three-view RGB + arm state -> 16 absolute actions."""
import argparse
import hashlib
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import torch
from torch import nn

from prepare import COLORS, SEED, write

VIEWS = ("end_effector", "ceiling_vertical", "ceiling_oblique")
WIDTH, HEIGHT, HORIZON = 8, 6, 16
BATCH, STEPS = 128, 4000
OUTPUT = HORIZON * 6


def net(dim):
    return nn.Sequential(nn.Linear(dim, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU(), nn.Linear(128, OUTPUT))


def extract(dataset, dest):
    import av
    import pyarrow.parquet as pq
    info = json.loads((dataset/"meta/info.json").read_bytes())
    eps = pq.ParquetFile(dataset/"meta/episodes/chunk-000/file-000.parquet").read().to_pylist()
    rgb = np.zeros((info["total_frames"], len(VIEWS), HEIGHT, WIDTH, 3), dtype=np.uint8)
    jobs = {}
    for ep in eps:
        for v, view in enumerate(VIEWS):
            key = "observation.images."+view
            prefix = "videos/"+key+"/"
            f = dataset/info["video_path"].format(video_key=key,chunk_index=ep[prefix+"chunk_index"],
                                                  file_index=ep[prefix+"file_index"])
            start = round(ep[prefix+"from_timestamp"]*info["fps"])
            jobs.setdefault((f, v), []).append((start, ep["dataset_from_index"], ep["length"]))
    def decode(job):
        (file, view), ranges = job
        ranges.sort()
        cursor = count = 0
        with av.open(str(file)) as movie:
            stream = movie.streams.video[0]
            stream.codec_context.thread_count = 2
            for frame in movie.decode(stream):
                t = round(frame.time*info["fps"])
                while cursor < len(ranges) and t >= ranges[cursor][0]+ranges[cursor][2]:
                    cursor += 1
                if cursor == len(ranges):
                    break
                start, offset, length = ranges[cursor]
                if start <= t < start+length:
                    idx = offset+t-start
                    if idx != offset + count - sum(r[2] for r in ranges[:cursor]):
                        raise ValueError(f"Skipped or duplicate PTS in {file.name}")
                    rgb[idx, view] = frame.reformat(width=WIDTH, height=HEIGHT, format="rgb24",
                                                    interpolation="AREA").to_ndarray()
                    count += 1
        assert count == sum(r[2] for r in ranges)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(decode, jobs.items()))
    np.save(dest, rgb, allow_pickle=False)


def inputs(root, fold, cache, onehot):
    import pyarrow.parquet as pq
    data = root/fold
    ep = pq.ParquetFile(data/"meta/episodes/chunk-000/file-000.parquet").read().to_pylist()
    table = pq.ParquetFile(data/"data/chunk-000/file-000.parquet").read()
    rgb = np.load(cache/f"{fold}.npy", allow_pickle=False)
    state = np.array(table["observation.state"].to_pylist(), dtype=np.float32)
    assert rgb.shape == (len(state),3,HEIGHT,WIDTH,3) and state.shape[1] == 6
    x = [rgb.reshape(len(rgb), -1).astype(np.float32)/255, state]
    if onehot:
        x.append(np.eye(3, dtype=np.float32)[np.asarray(table["task_index"])])
    return torch.from_numpy(np.column_stack(x)), torch.tensor(table["action"].to_pylist()), ep


def bundle_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train(root, output, cache, onehot, status):
    model = "rgb_mlp_task" if onehot else "rgb_mlp"
    dest = output/model
    dest.mkdir(parents=True, exist_ok=True)
    split_hash = bundle_sha(root/"split.json")
    status_file = dest/"status.json"
    if status_file.exists():
        saved = json.loads(status_file.read_bytes())
        if saved["split_hash"] != split_hash:
            raise ValueError("Existing run uses another split")
        if saved["state"] == "COMPLETE":
            held = [e for e in json.loads((root/"split.json").read_bytes())["episodes"] if e["fold"]=="eval"]
            required = ["policy.pt", "resume.pt", "history.json", *(f"episode_{e['id']}.npy" for e in held)]
            if all((dest/f).is_file() for f in required) and saved["steps"] == STEPS:
                return
    torch.set_num_threads(4)
    torch.manual_seed(SEED)
    random.seed(SEED)
    x, y, eps = inputs(root, "train", cache, onehot)
    tx, ty, ev = inputs(root, "eval", cache, onehot)
    xmean, xs = x.mean(0), x.std(0, unbiased=False).clamp_min(1e-3)
    ym, ys = y.mean(0), y.std(0, unbiased=False).clamp_min(1e-3)
    x, tx = (x-xmean)/xs, (tx-xmean)/xs
    y = (y-ym)/ys
    policy = net(x.shape[1])
    optimizer = torch.optim.AdamW(policy.parameters(), lr=0.001)
    valid = torch.cat([torch.arange(ep["dataset_from_index"], ep["dataset_to_index"]-HORIZON+1) for ep in eps])
    offsets = torch.arange(HORIZON)
    history, elapsed, first = [], 0., 0
    checkpoint = dest/"resume.pt"
    if checkpoint.exists():
        ck = torch.load(checkpoint, map_location="cpu", weights_only=True)
        if ck["split_hash"] != split_hash or ck["model"] != model:
            raise ValueError("Checkpoint provenance mismatch")
        policy.load_state_dict(ck["weights"])
        optimizer.load_state_dict(ck["optimizer"])
        torch.set_rng_state(ck["rng"])
        first, history, elapsed = ck["step"], ck["history"], ck["seconds"]
    start = time.monotonic()
    params = sum(p.numel() for p in policy.parameters())
    def log_state(state, step):
        duration = elapsed+time.monotonic()-start
        record = dict(state=state, steps=step, total=STEPS, seconds=duration, parameters=params,
                      batch=BATCH, seed=SEED, split_hash=split_hash,
                      eta_seconds=(duration/step)*(STEPS-step) if step else None)
        write(dest/"status.json", record)
        if status:
            from datetime import datetime, timedelta
            now = datetime.now().astimezone()
            eta = now+timedelta(seconds=record["eta_seconds"]) if step and step < STEPS else None
            with status.open("a", encoding="utf-8") as f:
                f.write(f"\n- {now.isoformat(timespec='seconds')} · {model} · {state} · {step}/{STEPS} · ETA {eta.isoformat(timespec='seconds') if eta else '—'}\n")
    log_state("RUNNING", first)
    # Validation at each milestone is descriptive and never changes optimization or checkpoint selection.
    anchors = torch.cat([torch.arange(e["dataset_from_index"], e["dataset_to_index"]-HORIZON+1, HORIZON) for e in ev])
    for step in range(first+1, STEPS+1):
        idx = valid[torch.randint(len(valid), (BATCH,))]
        targets = y[idx[:, None]+offsets].flatten(1)
        loss = (policy(x[idx])-targets).square().mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if step == 1 or step % 200 == 0:
            with torch.no_grad():
                val = ((policy(tx[anchors]).reshape(-1,HORIZON,6)-
                        (ty[anchors[:, None]+offsets]-ym)/ys).square().mean())
            history.append(dict(step=step, train=float(loss.detach()), val=float(val)))
        if step % 1000 == 0:
            temp = dest/"resume.tmp"
            torch.save(dict(weights=policy.state_dict(), optimizer=optimizer.state_dict(), rng=torch.get_rng_state(),
                            step=step, history=history, seconds=elapsed+time.monotonic()-start,
                            split_hash=split_hash, model=model), temp)
            os.replace(temp, checkpoint)
            log_state("CHECKPOINT", step)
    seconds = elapsed + time.monotonic()-start
    policy.eval()
    torch.save(dict(weights=policy.state_dict(), xmean=xmean, xs=xs, ym=ym, ys=ys, model=model,
                    dim=x.shape[1], split_hash=split_hash), dest/"policy.pt")
    split = json.loads((root/"split.json").read_bytes())
    ids = [r["id"] for r in split["episodes"] if r["fold"] == "eval"]
    with torch.no_grad():
        for ep, i in zip(ev, ids, strict=True):
            lo, hi = ep["dataset_from_index"], ep["dataset_to_index"]
            pred = policy(tx[lo:hi:HORIZON]).reshape(-1, 6)[:hi-lo]*ys+ym
            np.save(dest/f"episode_{i}.npy", pred.numpy(), allow_pickle=False)
    write(dest/"history.json", dict(history=history, seconds=seconds, parameters=params, steps=STEPS, batch=BATCH))
    log_state("COMPLETE", STEPS)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--extract", action="store_true")
    p.add_argument("--status", type=Path)
    args = p.parse_args()
    cache = args.output / ("rgb_"+bundle_sha(args.dataset/"split.json")[:16])
    cache.mkdir(parents=True, exist_ok=True)
    for fold in ("train", "eval"):
        path = cache/f"{fold}.npy"
        if args.extract and not path.exists():
            stage = cache/f"{fold}.part.npy"
            extract(args.dataset/fold, stage)
            os.replace(stage, path)
    for onehot in (False, True):
        train(args.dataset, args.output, cache, onehot, args.status)
