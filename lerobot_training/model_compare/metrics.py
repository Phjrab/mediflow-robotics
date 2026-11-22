"""Episode-level open-loop evaluation. All ruler scales and reference paths are fit on train."""
import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from prepare import write

FPS = 30
AXES = ["어깨 회전", "어깨 들기", "팔꿈치", "손목 굽힘", "손목 회전", "그리퍼"]


def closing(path):
    # Same opening and three-frame persistence criterion as grasp_diagnosis.closing_candidate.
    g = path[:, 5]
    opened = np.flatnonzero(g > g[0] + 10)
    if not len(opened):
        return None
    start = opened[0]
    peak = g[start]
    for i in range(start+1, len(g)-3):
        peak = max(peak, g[i])
        if i-start >= 12 and np.all(g[i:i+3] < peak-max(5, 0.2*(peak-g[0]))) and path[i, 2] < 70:
            return int(i)
    return None


def release(path, close):
    if close is None:
        return None
    g = path[:, 5]
    # Detect reopening after the local grasp has been established.
    low = g[close]
    for i in range(close+1, len(g)-2):
        low = min(low, g[i])
        if i >= close+15 and np.all(g[i:i+3] > low+10):
            return i
    return None


def placement(path, event, scale):
    # The second before the recorded release is the common time window for truth and predictions.
    if event is None or event < FPS:
        return None
    return path[event-FPS:event, :5] / scale


def load_fold(root, split, fold):
    rows = [r for r in split["episodes"] if r["fold"] == fold]
    table = pq.ParquetFile(root/fold/"data/chunk-000/file-000.parquet").read(
        columns=["episode_index", "frame_index", "action"])
    ids = np.asarray(table["episode_index"])
    frames = np.asarray(table["frame_index"])
    a = np.asarray(table["action"].to_pylist())
    result = {}
    for i, r in enumerate(rows):
        ix = ids == i
        assert np.array_equal(frames[ix], np.arange(r["frames"]))
        result[r["id"]] = (r["color"], a[ix])
    assert sum(len(v[1]) for v in result.values()) == len(table)
    return result


def score(root, prediction_dir=None):
    split = json.loads((root/"split.json").read_bytes())
    train = load_fold(root, split, "train")
    held = load_fold(root, split, "eval")
    assert not train.keys() & held.keys()
    scale = np.maximum(np.concatenate([a[:, :5] for _, a in train.values()]).std(0), 1e-6)
    refs = []
    for color, path in train.values():
        segment = placement(path, release(path, closing(path)), scale)
        if segment is not None:
            refs.append((color, segment))
    if set(x[0] for x in refs) != {"red", "green", "blue"}:
        raise ValueError("Missing placement reference color")
    rows, total = [], np.zeros(6)
    for ep, (color, truth) in held.items():
        pred = np.load(prediction_dir / f"episode_{ep}.npy", allow_pickle=False) if prediction_dir else truth
        if pred.shape != truth.shape or not np.isfinite(pred).all():
            raise ValueError(f"Invalid predictions for episode {ep}")
        gt, est = closing(truth), closing(pred)
        event = release(truth, gt)
        segment = placement(pred, event, scale)
        nearest = None if segment is None else min(refs, key=lambda ref: np.square(ref[1]-segment).mean())[0]
        error = np.abs(pred-truth)
        total += error.sum(0)
        rows.append(dict(episode=ep, color=color, frames=len(truth), mae=error.mean(0).tolist(),
                         close_true=gt, close_pred=est, release_true=event, nearest=nearest,
                         close_delta_s=None if gt is None or est is None else (est-gt)/FPS,
                         close_pose_deg=None if gt is None else error[gt, :5].tolist()))
    matched = [abs(r["close_delta_s"]) for r in rows if r["close_delta_s"] is not None]
    poses = [r["close_pose_deg"] for r in rows if r["close_pose_deg"] is not None]
    eligible = sum(r["nearest"] is not None for r in rows)
    return dict(kind="model" if prediction_dir else "ground_truth_metric_check", episodes=rows,
                mae=(total/sum(r["frames"] for r in rows)).tolist(), close_matched=len(matched),
                close_timing_mae_s=float(np.mean(matched)) if matched else None,
                close_pose_deg=np.mean(poses, axis=0).tolist() if poses else None,
                reference_count=len(refs), placement_eligible=eligible, denominator_all=len(rows),
                correct=sum(r["nearest"] == r["color"] for r in rows))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--predictions", type=Path)
    args = p.parse_args()
    write(args.output, score(args.dataset, args.predictions))
