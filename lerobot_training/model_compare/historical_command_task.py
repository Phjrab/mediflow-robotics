"""Matched one-frame command replay for the deployed b84 SmolVLA on saved RGB."""
import argparse
import gc
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np

from replay_protocol import HISTORICAL_SHA, RAW_VIEWS, digest

SPLIT_SHA = "9c97855b7abf137a5ca50d9af291b7f06556b6cd4b262f71f5bbdda7b8a13ff1"
FAIR_SHA = "765908e3ccb3c087c1eb9841248489f2cee320f849ba2272d7d2eb205ad8bcff"
COLORS = ("red", "green", "blue")


def observations(args):
    from historical_replay import cached_episode, samples
    from metrics import closing, load_fold
    from eval_blue import frames_for

    splitfile = args.fold/"split.json"
    if digest(splitfile) != SPLIT_SHA:
        raise ValueError("Unexpected frozen split")
    split = json.loads(splitfile.read_bytes())
    truth = load_fold(args.fold, split, "eval")
    anchor = json.loads(args.anchors.read_bytes())["results"]["act_recorded"]["rows"]
    expected = {row["episode"]: row for row in anchor}
    if len(expected) != len(anchor) or set(expected) != set(truth) or len(truth) != 21:
        raise ValueError("Anchor manifest did not cover the 21 frozen episodes")
    metadata, tables, verified = {}, {}, []
    for row, source in samples(split, args.mapping):
        if row["fold"] != "eval":
            continue
        episode = row["id"]
        root, info, meta, frames = cached_episode(args.cache, source, row, metadata, tables)
        color, actions = truth[episode]
        if color != row["color"] or not np.array_equal(actions, np.asarray([r["action"] for r in frames])):
            raise ValueError(f"Archived actions differ from frozen episode {episode}")
        close = closing(actions)
        reference = expected[episode]
        if close is None or close + 1 != reference["frame"] or color != reference["recorded"]:
            raise ValueError(f"Close+1 anchor differs for episode {episode}")
        frame = close + 1
        state = frames[frame]["observation.state"]
        rgb = frames_for(root, info, meta, frames, [frame])[frame]
        hashes = {key: hashlib.sha256(rgb[key].transpose(2, 0, 1).tobytes()).hexdigest() for key in RAW_VIEWS}
        mismatches = [key for key in RAW_VIEWS if hashes[key] != reference["rgb_chw_uint8_sha256"][key]]
        state_matches = np.array_equal(state, reference["state"])
        if not state_matches or (mismatches and not args.fair_checkpoint):
            raise ValueError(f"Canonical input differs in episode {episode}, state_matches={state_matches}, rgb={mismatches}")
        verified.append((dict(episode=episode, recorded=color, frame=frame, t_s=frame/30, state=state,
                              rgb_chw_uint8_sha256=hashes, canonical_rgb_mismatch_views=mismatches), rgb))
    if len(verified) != 21:
        raise ValueError("Expected 21 archived observations")
    return verified


def fair_engine(checkpoint, cfg):
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.factory import make_pre_post_processors
    from fast_postprocess import validate_post_config

    validate_post_config(json.loads((checkpoint/"policy_postprocessor.json").read_bytes()))
    policy_cfg = PreTrainedConfig.from_pretrained(checkpoint)
    policy_cfg.device = "cuda"
    policy_cfg.load_vlm_weights = False
    policy = SmolVLAPolicy.from_pretrained(checkpoint, config=policy_cfg, local_files_only=True,
                                          strict=True).cuda().eval()
    tokenizer = checkpoint/"tokenizer"
    if not tokenizer.is_dir():
        raise ValueError("Missing trained checkpoint tokenizer")
    pre, post = make_pre_post_processors(
        policy_cfg, str(checkpoint),
        preprocessor_overrides={"device_processor": {"device": "cuda"},
                                "tokenizer_processor": {"tokenizer_name": str(tokenizer.resolve())}},
    )
    return SimpleNamespace(policy=policy, pre=pre, post=post, config=cfg)


def replay(args, checkpoint, sha, model_id, model, verified, hashes, cfg_runtime):
    from checkpoint_amp import infer_with_saved_amp
    from fast_postprocess import bulk_actions
    from observation_contract import pack_observation
    import torch

    cfg = model.config
    output, rows = {}, []
    start = time.monotonic()
    with torch.inference_mode():
        for row, rgb in verified:
            episode, frame = row["episode"], row["frame"]
            seed = 101 + 100000*episode + frame
            generator = torch.Generator(device="cpu").manual_seed(seed)
            noise_cpu = torch.randn((1, 50, cfg["max_action_dim"]), generator=generator, dtype=torch.float32)
            noise_sha = hashlib.sha256(noise_cpu.contiguous().numpy().tobytes()).hexdigest()
            for color in COLORS:
                model.policy.reset()
                task = f"Put medicine bottle A into the {color} basket"
                obs = pack_observation(rgb, row["state"], task, torch, cfg_runtime)

                def predict():
                    prediction = model.policy.predict_action_chunk(model.pre(dict(obs)), noise=noise_cpu.to("cuda"))
                    return bulk_actions(model.post, prediction)

                result = np.asarray(infer_with_saved_amp(predict, torch, model.config), dtype=np.float32)
                if result.shape != (50, 6) or not np.isfinite(result).all():
                    raise ValueError(f"Invalid raw result for {episode} / {color}")
                output[f"{color}_{episode}"] = result
            rows.append(dict(row, noise_seed=seed, noise_sha256=noise_sha))
            print(f"{model_id} episode {episode}: {len(output)}/63 predictions", flush=True)
    if (len(output) != 63 or digest(checkpoint/"model.safetensors") != sha
            or hashes != {key: digest(checkpoint/key) for key in hashes}):
        raise ValueError("Checkpoint files changed during the matched replay")
    destination = args.output/model_id
    destination.mkdir(parents=True)
    historical = model_id == "historical_smolvla"
    engine = "deployed RealEngine" if historical else "strict trained checkpoint load with its bundled tokenizer"
    manifest = dict(model=model_id, weight_sha256=sha, historical=historical,
                    heldout_for_this_model=not historical, split_sha256=SPLIT_SHA, anchor_manifest_sha256=digest(args.anchors),
                    archive_mapping_sha256=digest(args.mapping), checkpoint_file_sha256=hashes,
                    source="Jetson original episode RGB", native_horizon=50, denoising_steps=cfg["num_steps"],
                    source_state_matches_canonical=True,
                    canonical_rgb_matching_views=sum(3-len(row["canonical_rgb_mismatch_views"]) for row in rows),
                    engine=engine, use_amp=True, amp_dtype=str(torch.get_autocast_dtype("cuda")),
                    seed_formula="101 + 100000 * episode + frame", device=torch.cuda.get_device_name(0),
                    torch=str(torch.__version__), transformers=version("transformers"),
                    rows=rows, seconds=time.monotonic()-start,
                    peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
                    peak_cuda_reserved_bytes=torch.cuda.max_memory_reserved())
    np.savez_compressed(destination/"predictions.npz", **output)
    (destination/"observation.json").write_text(json.dumps(manifest, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return rows


def run(args):
    sys.path[:0] = [str(args.runtime/p) for p in ("analysis", "live", "live/deps", "tools", "sim")]
    os.environ.update(SO101_CONFIG=str(args.runtime/"live/config.yaml"), HF_HUB_OFFLINE="1",
                      TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    from process_policy import RealEngine, deny_hardware

    sys.addaudithook(deny_hardware)
    from config_runtime import contract, load
    import torch

    checkpoints = [(args.checkpoint, HISTORICAL_SHA, "historical_smolvla")]
    if args.fair_checkpoint:
        checkpoints.append((args.fair_checkpoint, FAIR_SHA, "smolvla_jetson_archive"))
    engine_files = ("config.json", "policy_preprocessor.json", "policy_postprocessor.json")
    metadata = []
    for checkpoint, sha, model_id in checkpoints:
        if digest(checkpoint/"model.safetensors") != sha:
            raise ValueError(f"{model_id} weight digest mismatch")
        cfg = json.loads((checkpoint/"config.json").read_bytes())
        if cfg["chunk_size"] != 50 or cfg["n_action_steps"] != 50 or cfg["use_amp"] is not True:
            raise ValueError("Expected native 50-action saved AMP configuration")
        metadata.append((checkpoint, sha, model_id, cfg, {key: digest(checkpoint/key) for key in engine_files}))
    if len({cfg["max_action_dim"] for _, _, _, cfg, _ in metadata}) != 1:
        raise ValueError("Policy action noise dimensions differ")
    verified = observations(args)
    print("Verified all 21 archived episode states and close+1 timestamps", flush=True)
    cfg_runtime = contract(load())
    previous = None
    for checkpoint, sha, model_id, cfg, hashes in metadata:
        torch.cuda.reset_peak_memory_stats()
        engine = RealEngine({"checkpoint": str(checkpoint)}) if model_id == "historical_smolvla" else fair_engine(checkpoint, cfg)
        rows = replay(args, checkpoint, sha, model_id, engine, verified, hashes, cfg_runtime)
        if previous is not None and rows != previous:
            raise ValueError("Paired model RGB, state or noise records differ")
        previous = rows
        del engine
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("fold", "anchors", "cache", "mapping", "checkpoint", "runtime", "output"):
        parser.add_argument(f"--{argument}", required=True, type=Path)
    parser.add_argument("--fair-checkpoint", type=Path, help="Replay both models on identical Jetson archive frames")
    run(parser.parse_args())
