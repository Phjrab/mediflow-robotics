"""Counterfactual instruction check on recorded observations. Descriptive, not a rollout."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from compact import HORIZON, bundle_sha, inputs, net
from metrics import closing, load_fold, placement, release
from prepare import COLORS, write


def check(root, runs):
    split = json.loads((root/"split.json").read_bytes())
    train = load_fold(root, split, "train")
    scale = np.maximum(np.concatenate([a[:,:5] for _,a in train.values()]).std(0),1e-6)
    refs = []
    for c, a in train.values():
        s = placement(a, release(a, closing(a)), scale)
        if s is not None:
            refs.append((c,s))
    cache = runs/("rgb_"+bundle_sha(root/"split.json")[:16])
    results = {}
    for model in ("rgb_mlp", "rgb_mlp_task"):
        onehot = model.endswith("_task")
        x, truth, episodes = inputs(root, "eval", cache, onehot)
        b = torch.load(runs/model/"policy.pt", map_location="cpu", weights_only=True)
        policy = net(b["dim"]).eval()
        policy.load_state_dict(b["weights"])
        rows = []
        held = [r for r in split["episodes"] if r["fold"]=="eval"]
        with torch.inference_mode():
            for e, r in zip(episodes, held, strict=True):
                lo, hi = e["dataset_from_index"],e["dataset_to_index"]
                t = truth[lo:hi].numpy()
                event = release(t,closing(t))
                for color in COLORS:
                    if color == r["color"]:
                        continue
                    obs = x[lo:hi:HORIZON].clone()
                    if onehot:
                        obs[:,-3:] = torch.tensor([float(c==color) for c in COLORS])
                    a = (policy((obs-b["xmean"])/b["xs"]).reshape(-1,6)[:len(t)]*b["ys"]+b["ym"]).numpy()
                    s = placement(a,event,scale)
                    nearest = None if s is None else min(refs,key=lambda ref:np.square(s-ref[1]).mean())[0]
                    rows.append(dict(episode=r["id"],recorded=r["color"],requested=color,nearest=nearest))
        results[model] = dict(matches=sum(r["requested"]==r["nearest"] for r in rows), n=len(rows), rows=rows)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset",type=Path)
    p.add_argument("runs",type=Path)
    p.add_argument("output",type=Path)
    a = p.parse_args()
    torch.set_num_threads(2)
    write(a.output,check(a.dataset,a.runs))
