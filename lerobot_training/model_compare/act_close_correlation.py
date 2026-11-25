"""Compare ACT closure timing and cap-marker displacement in the same recorded wrist video."""
import argparse
import json
from pathlib import Path

import numpy as np

from metrics import FPS
from prepare import write
from wrist_close_pixels import measure


def correlation(x, y):
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def rank(values):
    """Midranks give tied observations equal rank."""
    values = np.asarray(values)
    return [np.sum(values < v) + (np.sum(values == v) + 1) / 2 for v in values]


def analyze(metrics, pixels):
    timing = {r["episode"]: r for r in metrics["episodes"] if r["color"] == "blue"}
    rows = []
    for item in pixels:
        if item["color"] != "blue":
            continue
        item = dict(item)
        record = timing[item["episode"]]
        a, b = item["recorded_close_frame"], item["predicted_close_frame"]
        if (a, b) != (record["close_true"], record["close_pred"]):
            raise ValueError("Mismatch between wrist frame and closure event")
        delta = None if a is None or b is None else b - a
        item.update(delta_frames=delta, delta_seconds=None if delta is None else delta/FPS)
        rows.append(item)
    if {r["episode"] for r in rows} != set(timing) or len(rows) != 7:
        raise ValueError("Expected seven distinct blue episodes")
    eligible = [r for r in rows if r["delta_frames"] is not None and r["delta_x_px"] is not None]
    x = np.array([r["delta_frames"] for r in eligible])
    y = np.array([r["delta_x_px"] for r in eligible])
    return dict(
        n=len(rows), n_paired=len(x), n_early=sum(r["delta_frames"] is not None and r["delta_frames"] < 0 for r in rows),
        n_early_right=int(np.sum((x < 0) & (y > 0))), n_early_left=int(np.sum((x < 0) & (y < 0))),
        n_early_zero=int(np.sum((x < 0) & (y == 0))),
        signed_pearson=correlation(x, y), signed_spearman=correlation(rank(x), rank(y)),
        absolute_pearson=correlation(abs(x), abs(y)), absolute_spearman=correlation(rank(abs(x)), rank(abs(y))),
        rows=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("metrics", type=Path, help="Basic ACT H16 metrics.json from the frozen fold")
    parser.add_argument("--replay-horizons", type=Path, help="Full-checkpoint stride replay_horizons.json")
    args = parser.parse_args()
    source = json.loads(args.metrics.read_bytes())
    if source["model"] != "act" or source["open_loop_horizon"] != 16:
        raise ValueError("Expected basic ACT H16 reference")
    configs = {16: source}
    if args.replay_horizons:
        replay = json.loads(args.replay_horizons.read_bytes())
        if replay["fps"] != FPS:
            raise ValueError("Unexpected FPS")
        configs.update({int(key): value["all_21"] for key, value in replay["strides"].items()})
        if set(configs) != {16, 25, 50, 100}:
            raise ValueError("Unexpected replay horizons")
    measurements = measure(args.dataset, None, configs, metric_records=configs)
    fields = ("episode", "color", "frames", "close_true")
    baseline = [[r[k] for k in fields] for r in source["episodes"]]
    result = {}
    for horizon, metrics in sorted(configs.items()):
        if [[r[k] for k in fields] for r in metrics["episodes"]] != baseline:
            raise ValueError("Replay evaluation fold or closure reference differs")
        result[horizon] = analyze(metrics, measurements["models"][horizon]["episodes"])
    write(args.output, dict(
        model="act", checkpoint_sha256=source["weight_sha256"], split_sha256=source["split_sha256"],
        fps=FPS, resolution=measurements["resolution"], marker_id=measurements["marker_id"],
        delta_time="predicted close minus recorded close; negative is early",
        delta_x="recorded cap-marker center at predicted close minus center at recorded close; positive is image right",
        horizons=result,
    ))


if __name__ == "__main__":
    main()
