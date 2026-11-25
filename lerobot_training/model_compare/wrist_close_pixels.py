"""Measure the cap marker's horizontal displacement between two timestamps in a recorded wrist view."""
import argparse
import json
from functools import lru_cache
from pathlib import Path

import av
import cv2
import numpy as np
import pyarrow.parquet as pq

from prepare import write

VIEW = "videos/observation.images.end_effector"
MODELS = ("smolvla", "act_task", "act", "diffusion", "xvla", "historical_smolvla", "rgb_mlp", "rgb_mlp_task")


def measure(dataset, report, models, *, metric_records=None):
    root = dataset/"eval"
    info = json.loads((root/"meta/info.json").read_bytes())
    split = json.loads((dataset/"split.json").read_bytes())
    held = [row for row in split["episodes"] if row["fold"] == "eval"]
    source = {row["episode_index"]: row for file in (root/"meta/episodes").rglob("*.parquet")
              for row in pq.ParquetFile(file).read().to_pylist()}
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))

    @lru_cache(maxsize=None)
    def marker_x(index, frame_index):
        if frame_index is None:
            return None
        if not 0 <= frame_index < held[index]["frames"] or info["fps"] != 30:
            raise ValueError("Invalid recorded frame")
        ep = source[index]
        path = root/VIEW/f"chunk-{ep[VIEW+'/chunk_index']:03d}"/f"file-{ep[VIEW+'/file_index']:03d}.mp4"
        target = ep[VIEW+"/from_timestamp"] + frame_index/info["fps"]
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            container.seek(round(target/stream.time_base), stream=stream)
            for frame in container.decode(stream):
                ts = float(frame.pts * stream.time_base)
                if ts < target-.5/info["fps"]:
                    continue
                if abs(ts-target) > .5/info["fps"] or (frame.width,frame.height) != (640,480):
                    raise ValueError("Recorded image size or timestamp mismatch")
                corners, ids, _ = detector.detectMarkers(frame.to_ndarray(format="gray"))
                matches = [] if ids is None else [q.reshape(4,2) for q,i in zip(corners,ids.ravel(),strict=True) if i==9]
                return float(matches[0][:,0].mean()) if len(matches)==1 else None
        raise ValueError("Missing recorded wrist frame")

    results = {}
    for model in models:
        if metric_records is not None:
            metrics = metric_records[model]
        elif model.startswith("rgb_mlp"):
            metrics = json.loads((report/(model+"_color_metrics.json")).read_bytes())["all_21"]
        else:
            metrics = json.loads((report/model/"metrics.json").read_bytes())
        rows = []
        for index,(ep,pred) in enumerate(zip(held,metrics["episodes"],strict=True)):
            if (ep["id"],ep["frames"],ep["color"]) != (pred["episode"],pred["frames"],pred["color"]):
                raise ValueError("Wrist camera episode and predictions do not match")
            reference = marker_x(index,pred["close_true"])
            observed = marker_x(index,pred["close_pred"])
            delta = None if reference is None or observed is None else observed-reference
            rows.append(dict(episode=ep["id"],color=ep["color"],recorded_close_frame=pred["close_true"],
                             predicted_close_frame=pred["close_pred"],reference_x_px=reference,
                             observed_x_at_predicted_time_px=observed,delta_x_px=delta))
        record = {"episodes":rows}
        for label,scope in (("blue_7",[r for r in rows if r["color"]=="blue"]),("all_21",rows)):
            dx = [r["delta_x_px"] for r in scope if r["delta_x_px"] is not None]
            record[label] = dict(matched=len(dx), total=len(scope),
                                 mae_px=float(np.mean(np.abs(dx))) if dx else None,
                                 signed_mean_px=float(np.mean(dx)) if dx else None)
        results[model] = record
    return dict(view=VIEW,resolution=[640,480],dictionary="DICT_4X4_50",marker_id=9,
                measurement="observed cap center x at predicted closure time minus observed x at recorded closure; positive is image right",
                center_definition="arithmetic mean of four detected marker corners",
                counterfactual_wrist_view="unobserved",models=results)


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset",type=Path)
    p.add_argument("report",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--models",nargs="+",choices=MODELS,default=MODELS)
    args=p.parse_args()
    write(args.output,measure(args.dataset,args.report,args.models))
