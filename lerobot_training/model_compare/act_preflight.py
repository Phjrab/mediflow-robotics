"""Recorded ACT predictions after demonstrated closing and at fixed replay strides."""
import argparse
from collections import Counter
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow.parquet as pq
import torch

from evaluate import ReplayPolicy, checkpoint_for, observations, predict
from metrics import FPS, closing, load_fold, release, score
from prepare import COLORS, write
from replay_protocol import RAW_VIEWS, digest

CHUNK = 100
TAIL = 10
STRIDES = (25, 50, 100)
METHODS = ("last_action", "endpoint", "displacement", "pre_release_endpoint")
SMALL_MARGIN = 0.10
SMALL_MOVEMENT = 0.10


def read_states(root, fold, expected_frames):
    path = root/fold/"data/chunk-000/file-000.parquet"
    table = pq.ParquetFile(path).read(columns=["observation.state"])
    state = np.asarray(table["observation.state"].to_pylist())
    if state.shape != (expected_frames, 6) or not np.isfinite(state).all():
        raise ValueError("Invalid six-axis recorded state")
    return state


def direction_features(chunk, start):
    endpoint = chunk[-TAIL:, :5].mean(0)
    return endpoint, endpoint-start[:5]


def training_references(split, train, state):
    references = []
    start = 0
    for row in split["episodes"]:
        if row["fold"] != "train":
            continue
        episode = row["id"]
        color, truth = train[episode]
        close = closing(truth)
        opened = release(truth, close)
        frame = close+1 if close is not None else None
        if frame is not None and frame+CHUNK <= len(truth) and opened is not None and opened >= TAIL:
            initial = state[start+frame]
            endpoint, movement = direction_features(truth[frame:frame+CHUNK], initial)
            references.append(dict(episode=episode, color=color, endpoint=endpoint, displacement=movement,
                                   last_action=truth[frame+CHUNK-1, :5],
                                   pre_release_endpoint=truth[opened-TAIL:opened, :5].mean(0)))
        start += len(truth)
    if start != len(state) or {entry["color"] for entry in references} != set(COLORS):
        raise ValueError("Incomplete training-only direction references")
    return references


def classify(endpoint, movement, last_action, refs, scale):
    result = {}
    values = {"last_action": last_action, "endpoint": endpoint, "displacement": movement,
              "pre_release_endpoint": endpoint}
    for method, value in values.items():
        distances = {}
        for color in COLORS:
            # Use a per-color centroid so unequal color counts do not give one color
            # more chances to win the nearest-neighbor decision.
            centroid = np.stack([x[method] for x in refs if x["color"] == color]).mean(0)
            distances[color] = float(np.sqrt(np.mean(np.square((value-centroid)/scale))))
        first, second, _ = sorted(distances, key=distances.get)
        margin = (distances[second]-distances[first])/max(distances[second], 1e-12)
        result[method] = dict(color=first, distance=distances, margin=margin,
                              ambiguous=margin <= SMALL_MARGIN)
    rms = float(np.sqrt(np.mean(np.square(movement/scale))))
    result["movement_rms_normalized"] = rms
    result["small_movement"] = rms < SMALL_MOVEMENT
    return result


def method_summary(rows, criterion="recorded"):
    summary = {}
    for method in METHODS:
        per_color = {}
        for color in COLORS:
            relevant = [r for r in rows if r["recorded"] == color]
            per_color[color] = {outcome: sum(r["classification"][method]["color"] == outcome
                                            for r in relevant) for outcome in COLORS}
        summary[method] = dict(
            n=len(rows), confusion_from_recorded=per_color,
            matches=sum(r["classification"][method]["color"] == r[criterion] for r in rows),
            blue_all=sum(r["classification"][method]["color"] == "blue" for r in rows),
            blue_from_nonblue=sum(r["recorded"] != "blue" and r["classification"][method]["color"] == "blue" for r in rows),
            nonblue_n=sum(r["recorded"] != "blue" for r in rows),
            ambiguous=sum(r["classification"][method]["ambiguous"] for r in rows),
            small_movement=sum(r["classification"]["small_movement"] for r in rows),
        )
    return summary


def direction_row(episode, recorded, requested, frame, chunk, state, refs, scale):
    if chunk.shape != (CHUNK, 6):
        raise ValueError("Expected a full native 100x6 ACT action chunk")
    endpoint, movement = direction_features(chunk, state)
    return dict(episode=episode, recorded=recorded, requested=requested, frame=frame,
                t_s=frame/FPS, state=state[:6].tolist(), endpoint_arm_degrees=endpoint.tolist(),
                final_target_arm_degrees=chunk[-1, :5].tolist(),
                movement_arm_degrees=movement.tolist(), classification=classify(endpoint, movement, chunk[-1, :5], refs, scale))


def read_observation(dataset, offset, episode_index, frame):
    item = dataset[offset+frame]
    if int(item["episode_index"]) != episode_index or int(item["frame_index"]) != frame:
        raise ValueError("Recorded episode/frame is misaligned")
    return item


def camera_hashes(item):
    hashes = {}
    for view in RAW_VIEWS:
        rgb = item["observation.images."+view]
        if rgb.shape != (3,480,640) or rgb.dtype != torch.uint8:
            raise ValueError(f"Expected one uint8 original RGB frame for {view}")
        hashes[view] = hashlib.sha256(rgb.contiguous().numpy().tobytes()).hexdigest()
    return hashes

def save_replay_summary(root, output, strides_paths, truth):
    result = {}
    for stride, dest in strides_paths.items():
        metrics = {label: score(root, dest, color) for label, color in (("blue_7","blue"), ("all_21",None))}
        for scope in metrics.values():
            for row in scope["episodes"]:
                recorded = truth[row["episode"]][1]
                actions = np.load(dest/f"episode_{row['episode']}.npy", allow_pickle=False)
                gt, est = row["close_true"], row["close_pred"]
                row["close_true_s"] = gt/FPS if gt is not None else None
                row["close_pred_s"] = est/FPS if est is not None else None
                row["close_gripper_error_points"] = float(abs(actions[gt, 5]-recorded[gt, 5])) if gt is not None else None
                if gt is not None:
                    sl = slice(max(0, gt-15), min(len(recorded), gt+16))
                    row["close_window_gripper_mae_points"] = float(np.abs(actions[sl, 5]-recorded[sl, 5]).mean())
                else:
                    row["close_window_gripper_mae_points"] = None
            for field in ("close_gripper_error_points", "close_window_gripper_mae_points"):
                present = [row[field] for row in scope["episodes"] if row[field] is not None]
                scope[field] = float(np.mean(present)) if present else None
                scope[field+"_n"] = len(present)
        result[str(stride)] = metrics
    write(output/"replay_horizons.json", dict(fps=FPS, strides=result))


def run(args):
    root = args.dataset_root
    args.output.mkdir(parents=True, exist_ok=True)
    paths = {name: root/("onehot" if name == "act_task" else "standard") for name in ("act","act_task")}
    split, _, _, _ = checkpoint_for(argparse.Namespace(
        model="act", dataset=paths["act"], runs=args.runs, checkpoint=None, step=args.act_step))
    held_rows = [r for r in split["episodes"] if r["fold"] == "eval"]
    if len(held_rows) != 21 or Counter(r["color"] for r in held_rows) != dict.fromkeys(COLORS,7):
        raise ValueError("Unexpected held-out episode split")
    train = load_fold(paths["act"], split, "train")
    truth = load_fold(paths["act"], split, "eval")
    train_state = read_states(paths["act"], "train", sum(len(path) for _,path in train.values()))
    held_state = read_states(paths["act"], "eval", sum(len(path) for _,path in truth.values()))
    scale = np.maximum(np.concatenate([path[:, :5] for _,path in train.values()]).std(0), 1e-6)
    refs = training_references(split, train, train_state)
    loo = []
    for row in refs:
        other_refs = [ref for ref in refs if ref["episode"] != row["episode"]]
        loo_scale = np.maximum(np.concatenate([path[:, :5] for ep,(_,path) in train.items()
                                               if ep != row["episode"]]).std(0), 1e-6)
        result = classify(row["endpoint"], row["displacement"], row["last_action"], other_refs, loo_scale)
        # The pre-release control uses a true pre-release target for its own LOO check.
        result["pre_release_endpoint"] = classify(row["pre_release_endpoint"], row["displacement"],
                                                  row["last_action"], other_refs, loo_scale)["pre_release_endpoint"]
        loo.append(dict(episode=row["episode"], recorded=row["color"], classification=result))
    results, raw = {}, {}
    metadata = {}
    episode_camera_hashes = {}
    with TemporaryDirectory(dir=args.output) as tmp, torch.inference_mode():
        strides_paths = {stride: Path(tmp)/str(stride) for stride in STRIDES}
        for path in strides_paths.values():
            path.mkdir()
        for name, want in (("act", args.act_step), ("act_task", args.act_task_step)):
            use_args = argparse.Namespace(model=name, dataset=paths[name], runs=args.runs, checkpoint=None,
                                          step=want, device=args.device, xvla_native=False)
            actual_split, checkpoint, step, target = checkpoint_for(use_args)
            if actual_split != split:
                raise ValueError("Both ACT checkpoints must use the identical split")
            replay = ReplayPolicy(use_args, checkpoint)
            if replay.cfg.chunk_size != CHUNK or replay.cfg.type != "act":
                raise ValueError("Expected a 100-action ACT checkpoint")
            dataset = observations(paths[name], replay)
            if len(dataset) != len(held_state):
                raise ValueError("Eval frame count mismatch")
            metadata[name] = dict(step=step, target_steps=target, weight_sha256=replay.weight_sha)
            rows, forced_rows, truth_rows = [], [], []
            offset = 0
            for local, row in enumerate(held_rows):
                episode, color, length = row["id"], row["color"], row["frames"]
                recorded = truth[episode][1]
                close = closing(recorded)
                frame = close+1 if close is not None else None
                if frame is None or frame+CHUNK > length:
                    rows.append(dict(episode=episode, recorded=color, status="ineligible_no_aligned_truth_window"))
                    forced_rows.append(rows[-1])
                else:
                    item = read_observation(dataset, offset, local, frame)
                    state = item["observation.state"].reshape(-1).cpu().numpy()
                    rgb_hashes = camera_hashes(item)
                    if not np.array_equal(state[:6], held_state[offset+frame]):
                        raise ValueError("Inputs do not share the original recorded arm state")
                    if episode_camera_hashes.setdefault(episode, rgb_hashes) != rgb_hashes:
                        raise ValueError("ACT and ACT + task RGB inputs differ at the same frame")
                    actual = predict(replay, item, color, episode, frame, CHUNK)
                    forced = predict(replay, item, "blue", episode, frame, CHUNK)
                    normal = direction_row(episode, color, color, frame, actual, state, refs, scale)
                    swap = direction_row(episode, color, "blue", frame, forced, state, refs, scale)
                    normal["rgb_chw_uint8_sha256"] = rgb_hashes
                    swap["rgb_chw_uint8_sha256"] = rgb_hashes
                    swap["arm_change_mae_deg"] = float(np.abs(actual[:, :5]-forced[:, :5]).mean())
                    swap["max_change_any_axis"] = float(np.abs(actual-forced).max())
                    rows.append(normal)
                    forced_rows.append(swap)
                    raw[f"{name}_recorded_{episode}"] = actual
                    raw[f"{name}_forced_blue_{episode}"] = forced
                    if name == "act":
                        p = recorded[frame:frame+CHUNK]
                        truth_row = direction_row(episode, color, color, frame, p, state, refs, scale)
                        opened = release(recorded, close)
                        truth_row["release_frame"] = opened
                        truth_row["release_within_chunk"] = opened is not None and frame <= opened < frame+CHUNK
                        truth_rows.append(truth_row)
                        raw[f"truth_close_plus_one_{episode}"] = p
                if name == "act":
                    # Identical inputs/outputs at every shared frame. Discard each
                    # suffix once the chosen amount has been executed open-loop.
                    every = {
                        frame: predict(replay, read_observation(dataset,offset,local,frame), color, episode, frame, CHUNK)
                        for frame in range(0,length,min(STRIDES))
                    }
                    for stride in STRIDES:
                        stitched = np.concatenate([every[frame][:min(stride,length-frame)]
                                                   for frame in range(0,length,stride)])
                        np.save(strides_paths[stride]/f"episode_{episode}.npy", stitched, allow_pickle=False)
                        raw[f"act_stride_{stride}_{episode}"] = stitched
                offset += length
                print(f"{name}: {local+1}/21 episode {episode}", flush=True)
            for key, report_rows, criterion in (("recorded",rows,"recorded"), ("forced_blue",forced_rows,"requested")):
                observed = [entry for entry in report_rows if "classification" in entry]
                results[f"{name}_{key}"] = dict(rows=report_rows, eligible=len(observed), total=len(report_rows),
                                               summary=method_summary(observed, criterion))
            if truth_rows:
                results["heldout_truth_positive_control"] = dict(rows=truth_rows, eligible=len(truth_rows),
                    total=len(held_rows), summary=method_summary(truth_rows))
            del replay, dataset
            if args.device == "cuda":
                torch.cuda.empty_cache()
        save_replay_summary(paths["act"], args.output, strides_paths, truth)
    np.savez_compressed(args.output/"predictions.npz", **raw)
    write(args.output/"direction.json", dict(
        split_sha256=digest(paths["act"]/"split.json"), fps=FPS, native_chunk=CHUNK, tail_steps=TAIL,
        observation="the original 3 RGB + state at one frame after recorded close", checkpoints=metadata,
        reference_counts=dict(Counter(row["color"] for row in refs)), scale_arm_degrees=scale.tolist(),
        ambiguous_margin_le=SMALL_MARGIN, small_movement_normalized_lt=SMALL_MOVEMENT,
        train_leave_one_out=dict(rows=loo, summary=method_summary(loo)), results=results,
    ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("dataset_root", "runs", "output"):
        parser.add_argument(key, type=Path)
    parser.add_argument("--act-step", type=int, default=40244)
    parser.add_argument("--act-task-step", type=int, default=40163)
    parser.add_argument("--device", choices=["cuda","cpu"], default="cuda")
    run(parser.parse_args())
