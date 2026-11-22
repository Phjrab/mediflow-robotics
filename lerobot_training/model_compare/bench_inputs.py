"""Export one real held-out observation per episode for the compact-policy Jetson probe."""
import argparse
import json
import shutil
from pathlib import Path

import numpy as np

from compact import bundle_sha, inputs
from metrics import closing


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("runs", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cache = args.runs / ("rgb_"+bundle_sha(args.dataset/"split.json")[:16])
    samples = {}
    for model, task in (("rgb_mlp",False), ("rgb_mlp_task",True)):
        x, actions, episodes = inputs(args.dataset, "eval", cache, task)
        rows = []
        for e in episodes:
            lo, hi = e["dataset_from_index"], e["dataset_to_index"]
            event = closing(actions[lo:hi].numpy())
            rows.append(lo + (event if event is not None else (hi-lo)//2))
        samples[model] = x[rows].numpy()
        shutil.copyfile(args.runs/model/"policy.pt", args.output/(model+".pt"))
    np.savez(args.output/"observations.npz", **samples)
