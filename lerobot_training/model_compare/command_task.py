"""Paired, recorded close+1 command replay and train-only direction scoring."""
import argparse
from contextlib import nullcontext
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import time

import numpy as np
import torch
from torch.utils.data import default_collate

from act_preflight import (
    METHODS, SMALL_MARGIN, SMALL_MOVEMENT, TAIL, camera_hashes, classify, direction_row,
    method_summary, read_observation, read_states, training_references,
)
from evaluate import ReplayPolicy, checkpoint_for, observations
from train_queue import REPORT
from metrics import FPS, closing, load_fold, release
from prepare import COLORS, write
from replay_protocol import HISTORICAL, digest

CHUNK = 50


def command(color):
    return f"Put medicine bottle A into the {color} basket"


def seed_for(episode, frame):
    return 101 + 100000*episode + frame


def validate_chunk(chunk):
    if chunk.shape != (CHUNK, 6) or not np.isfinite(chunk).all():
        raise ValueError("Expected a finite native 50x6 SmolVLA output")


def replay(args):
    historical = args.checkpoint is not None
    args.model = HISTORICAL if historical else "smolvla"
    args.xvla_native = False
    if args.cpu_threads is not None:
        torch.set_num_threads(args.cpu_threads)
    if not historical and args.step is None:
        args.step = 20000
    split, checkpoint, step, target = checkpoint_for(args)
    held = load_fold(args.dataset, split, "eval")
    states = read_states(args.dataset, "eval", sum(len(a) for _,a in held.values()))
    actor = ReplayPolicy(args, checkpoint)
    if actor.cfg.type != "smolvla" or actor.cfg.chunk_size != CHUNK or actor.cfg.n_obs_steps != 1:
        raise ValueError("Expected a native 50-action, one-observation SmolVLA")
    dataset = observations(args.dataset, actor)
    if len(dataset) != len(states) or len(held) != 21:
        raise ValueError("Unexpected held-out fold length")
    outputs, rows = {}, []
    started = time.monotonic()
    start = 0
    with torch.inference_mode():
        for index, (episode, (recorded, truth)) in enumerate(held.items()):
            close = closing(truth)
            frame = None if close is None else close+1
            if frame is None or frame+CHUNK > len(truth):
                raise ValueError(f"Episode {episode} has no aligned closing+1 window")
            item = read_observation(dataset, start, index, frame)
            rgb_hashes = camera_hashes({key: value.squeeze(0) if value.ndim == 4 else value
                                         for key,value in item.items() if key.startswith("observation.images.")})
            state = item["observation.state"].reshape(-1)
            if not np.array_equal(state.numpy(), states[start+frame]):
                raise ValueError("Recorded state differs from frozen eval parquet")
            seed = seed_for(episode, frame)
            noise_cpu = torch.randn((1, CHUNK, actor.cfg.max_action_dim), dtype=torch.float32,
                                    generator=torch.Generator(device="cpu").manual_seed(seed))
            noise = noise_cpu.to(args.device)
            raw = {key: (value.float()/255 if value.dtype == torch.uint8 else value)
                   for key, value in item.items() if key.startswith("observation.") and not key.endswith("_is_pad")}
            token_hashes = {}
            for color in COLORS:
                actor.reset()
                inputs = actor.pre(default_collate([{**raw, "task": command(color)}]))
                tokens = inputs["observation.language.tokens"].detach().cpu().contiguous().numpy()
                token_hashes[color] = hashlib.sha256(tokens.tobytes()).hexdigest()
                ctx = torch.autocast("cuda") if actor.cfg.use_amp and args.device == "cuda" else nullcontext()
                with ctx:
                    prediction = actor.policy.predict_action_chunk(inputs, noise=noise.clone())
                chunk = actor.post(prediction.float())[0].detach().cpu().float().numpy()
                validate_chunk(chunk)
                outputs[f"{color}_{episode}"] = chunk
            if len(set(token_hashes.values())) != 3:
                raise ValueError("Color instructions did not produce three different token sequences")
            rows.append(dict(episode=episode, recorded=recorded, frame=frame, t_s=frame/FPS,
                             state=state.tolist(), rgb_chw_uint8_sha256=rgb_hashes, noise_seed=seed,
                             noise_sha256=hashlib.sha256(noise_cpu.numpy().tobytes()).hexdigest(),
                             token_sha256_by_command=token_hashes))
            start += len(truth)
            print(f"{args.model} {index+1}/21 episode {episode}, {time.monotonic()-started:.1f}s", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    write(args.output/"observation.json", dict(model=args.model, weight_sha256=actor.weight_sha,
          checkpoint_step=step, target_steps=target, split_sha256=digest(args.dataset/"split.json"),
          trained_episodes=137 if historical else 116, evaluated_episodes=21,
          historical=historical, heldout_for_this_model=not historical, device=args.device,
          chunk_size=CHUNK, num_denoising_steps=actor.cfg.num_steps, amp=actor.cfg.use_amp,
          torch_version=str(torch.__version__), transformers_version=version("transformers"),
          lerobot_version=version("lerobot"), elapsed_s=time.monotonic()-started, rows=rows,
          precision="saved CUDA AMP" if actor.cfg.use_amp and args.device == "cuda" else "without CUDA AMP"))
    np.savez_compressed(args.output/"predictions.npz", **outputs)


def add_requested_confusion(summary, rows):
    for method in METHODS:
        summary[method]["confusion_from_requested"] = {
            requested: {color:sum(row["requested"]==requested and row["classification"][method]["color"]==color
                                  for row in rows) for color in COLORS}
            for requested in COLORS
        }
        summary[method]["distinct_episodes"] = len({row["episode"] for row in rows})
        changes = [row for row in rows if row.get("changed_from_original") is not None]
        summary[method]["prediction_label_changed_from_original"] = (
            sum(row["changed_from_original"][method] for row in changes) if changes else None
        )
    return summary


def score_commands(args):
    split_file = args.dataset/"split.json"
    split = json.loads(split_file.read_bytes())
    if split != json.loads((REPORT/"split.json").read_bytes()):
        raise ValueError("Direction scoring requires the original frozen split")
    train = load_fold(args.dataset, split, "train")
    held = load_fold(args.dataset, split, "eval")
    if len(train) != 116 or len(held) != 21:
        raise ValueError("Expected the frozen 116/21 split")
    train_states = read_states(args.dataset, "train",sum(len(a) for _,a in train.values()))
    held_states = read_states(args.dataset, "eval",sum(len(a) for _,a in held.values()))
    scale = np.maximum(np.concatenate([a[:, :5] for _,a in train.values()]).std(0),1e-6)
    refs = training_references(split, train, train_states, CHUNK)
    loo = []
    for ref in refs:
        remaining = [r for r in refs if r["episode"] != ref["episode"]]
        loo_scale = np.maximum(np.concatenate([a[:, :5] for ep,(_,a) in train.items()
                                               if ep != ref["episode"]]).std(0),1e-6)
        record = classify(ref["endpoint"], ref["displacement"], ref["last_action"], remaining, loo_scale)
        record["pre_release_endpoint"] = classify(ref["pre_release_endpoint"],ref["displacement"],
                                                   ref["last_action"],remaining,loo_scale)["pre_release_endpoint"]
        loo.append(dict(episode=ref["episode"], recorded=ref["color"], classification=record))

    manifest = json.loads(args.observation.read_bytes())
    by_episode = {row["episode"]:row for row in manifest["rows"]}
    if len(manifest["rows"]) != 21 or set(by_episode) != set(held):
        raise ValueError("Observation inventory differs from the frozen eval fold")
    if manifest.get("split_sha256",digest(split_file)) != digest(split_file):
        raise ValueError("Observation split SHA differs")
    truth_rows, trials = [], []
    start = 0
    with np.load(args.predictions,allow_pickle=False) as arrays:
        if set(arrays.files) != {f"{color}_{episode}" for color in COLORS for episode in held}:
            raise ValueError("Expected 63 paired trials from 21 episodes")
        for episode,(recorded,truth) in held.items():
            anchor = by_episode[episode]
            close = closing(truth)
            if close is None or close+1+CHUNK > len(truth):
                raise ValueError(f"Episode {episode} has no aligned closing+1 score window")
            frame = close+1
            state = held_states[start+frame]
            if (anchor["frame"] != frame or anchor["recorded"] != recorded
                or anchor["noise_seed"] != seed_for(episode,frame)
                or not np.array_equal(state,np.asarray(anchor["state"]))):
                raise ValueError(f"Anchor mismatch for episode {episode}")
            original = arrays[f"{recorded}_{episode}"]
            validate_chunk(original)
            truth_row = direction_row(episode,recorded,recorded,frame,truth[frame:frame+CHUNK],
                                      state,refs,scale,CHUNK)
            open_at = release(truth,close)
            truth_row["release_within_chunk"] = open_at is not None and frame <= open_at < frame+CHUNK
            truth_rows.append(truth_row)
            local = []
            for requested in COLORS:
                prediction = arrays[f"{requested}_{episode}"]
                validate_chunk(prediction)
                comparison = direction_row(episode,recorded,requested,frame,prediction,state,refs,scale,CHUNK)
                comparison["arm_change_mae_deg"] = float(np.abs(prediction[:, :5]-original[:, :5]).mean())
                comparison["gripper_change_mae_points"] = float(np.abs(prediction[:, 5]-original[:, 5]).mean())
                local.append(comparison)
            reference = next(row for row in local if row["recorded"]==row["requested"])
            for row in local:
                row["changed_from_original"] = {
                    method: row["classification"][method]["color"] != reference["classification"][method]["color"]
                    for method in METHODS
                }
            trials.extend(local)
            start += len(truth)
    scopes = {
        "all": trials,
        "original": [r for r in trials if r["requested"]==r["recorded"]],
        "swap": [r for r in trials if r["requested"]!=r["recorded"]],
        "requested_blue": [r for r in trials if r["requested"]=="blue"],
        "original_blue": [r for r in trials if r["recorded"]==r["requested"]=="blue"],
        "nonblue_to_blue": [r for r in trials if r["recorded"]!="blue" and r["requested"]=="blue"],
    }
    write(args.output,dict(model=manifest["model"], weight_sha256=manifest["weight_sha256"],
          historical=manifest.get("historical"), split_sha256=digest(split_file), trial_count=63,
          distinct_episodes=21, fps=FPS, chunk_size=CHUNK, tail_steps=TAIL,
          ambiguous_margin_le=SMALL_MARGIN, small_movement_normalized_lt=SMALL_MOVEMENT,
          scale_arm_degrees=scale.tolist(),
          reference_counts={c:sum(r["color"]==c for r in refs) for c in COLORS},
          summary={key:add_requested_confusion(method_summary(rows,"requested"),rows) for key,rows in scopes.items()},
          heldout_truth_positive_control=dict(summary=method_summary(truth_rows),rows=truth_rows),
          train_leave_one_out=dict(summary=method_summary(loo),rows=loo), trials=trials))


def compare_anchors(args):
    first = json.loads(args.first.read_bytes())
    second = json.loads(args.second.read_bytes())
    by_model = [{row["episode"]: row for row in run["rows"]} for run in (first,second)]
    if first["split_sha256"] != second["split_sha256"] or set(by_model[0]) != set(by_model[1]) or len(by_model[0]) != 21:
        raise ValueError("The two inventories do not describe the same frozen split")
    fields = ("recorded", "frame", "state", "rgb_chw_uint8_sha256", "noise_seed", "noise_sha256")
    differences = {
        field: [episode for episode in sorted(by_model[0])
                if by_model[0][episode].get(field) is None
                or by_model[0][episode].get(field) != by_model[1][episode].get(field)]
        for field in fields
    }
    write(args.output, dict(
        models=[run["model"] for run in (first,second)], split_sha256=first["split_sha256"],
        model_observation_sha256=[digest(source) for source in (args.first,args.second)],
        episodes=21, all_anchors_aligned=not any(differences.values()), differences=differences,
        same_runtime=all(first.get(key)==second.get(key) for key in ("device","torch_version","precision")),
    ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="task",required=True)
    infer = sub.add_parser("infer")
    for name in ("dataset","runs","output"):
        infer.add_argument(name,type=Path)
    infer.add_argument("--device",choices=["cuda","cpu"],default="cuda")
    checkpoint = infer.add_mutually_exclusive_group()
    checkpoint.add_argument("--step",type=int,help="Official run step (default: 20000)")
    checkpoint.add_argument("--checkpoint",type=Path,help="Exact deployed b84 historical reference")
    infer.add_argument("--cpu-threads",type=int)
    assess = sub.add_parser("score")
    for name in ("dataset","observation","predictions","output"):
        assess.add_argument(name,type=Path)
    compare = sub.add_parser("compare-anchors")
    for name in ("first","second","output"):
        compare.add_argument(name,type=Path)
    args = parser.parse_args()
    {"infer":replay, "score":score_commands, "compare-anchors":compare_anchors}[args.task](args)
