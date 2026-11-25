"""Score completed checkpoints on the frozen fold using recorded observations only."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import default_collate

from metrics import FPS, closing, load_fold, placement, release, score
from prepare import COLORS, write
from replay_protocol import HISTORICAL, HISTORICAL_SHA, HORIZON, RAW_VIEWS, digest
from train_queue import REPORT, SCHEDULE, checkpoints


def checkpoint_for(args):
    root = args.dataset
    split = json.loads((root/"split.json").read_bytes())
    if split != json.loads((REPORT/"split.json").read_bytes()):
        raise ValueError("Dataset does not match the frozen split")
    if args.model == HISTORICAL:
        if args.checkpoint is None or digest(args.checkpoint/"model.safetensors") != HISTORICAL_SHA:
            raise ValueError("Historical SmolVLA requires the exact b84b15c7 weights")
        if args.step is not None:
            raise ValueError("--step applies only to official runs")
        return split, args.checkpoint, None, None
    if args.checkpoint is not None:
        raise ValueError("Official checkpoints must come from the verified run directory")
    name = args.model
    provenance = json.loads((args.runs/(name+".provenance.json")).read_bytes())
    if (provenance["split_sha"] != digest(REPORT/"split.json")
        or provenance["train_stats_sha"] != digest(root/"train/meta/stats.json")
        or provenance["name"] != name or provenance["steps"] != SCHEDULE[name][0]):
        raise ValueError("Checkpoint split/stats/schedule mismatch")
    target = SCHEDULE[name][0]
    verified = [path for path in checkpoints(args.runs/name, provenance) if 0 < int(path.name) <= target]
    if args.step is not None:
        verified = [path for path in verified if int(path.name) == args.step]
    if not verified:
        requested = f" at step {args.step}" if args.step is not None else ""
        raise ValueError(f"No queue-verified checkpoint{requested} for {name}")
    selected = verified[-1]
    return split, selected/"pretrained_model", int(selected.name), target


def replay_horizon(cfg):
    horizon = min(HORIZON, cfg.n_action_steps)
    if horizon < 1:
        raise ValueError("Policy must deliver at least one action")
    return horizon


def verify_native_xvla(dataset, checkpoint, cfg, rename):
    """Validate the original trained processor path used for CUDA replay only."""
    from safetensors.torch import load_file
    from xvla_adapter import JOINTS

    expected_cameras = ("observation.images.image", "observation.images.image2", "observation.images.image3")
    expected_rename = dict(zip(("observation.images."+view for view in RAW_VIEWS), expected_cameras, strict=True))
    cfg_json = json.loads((checkpoint/"config.json").read_bytes())
    if (cfg_json["type"] != "xvla" or cfg.action_mode != "auto" or cfg.max_action_dim != 20
        or cfg.n_obs_steps != 1 or cfg.num_image_views != 3 or not cfg.use_proprio
        or tuple(cfg.image_features) != expected_cameras or rename != expected_rename
        or cfg_json["input_features"]["observation.state"]["shape"] != [6]
        or cfg_json["output_features"]["action"]["shape"] != [6]):
        raise ValueError("X-VLA trained three-camera auto action space does not match the replay contract")
    expected_norms = {"STATE": "IDENTITY", "ACTION": "MEAN_STD", "VISUAL": "IDENTITY"}
    if cfg_json["normalization_mapping"] != expected_norms:
        raise ValueError("X-VLA action or state normalization differs from training")
    for fold in ("train", "eval"):
        info = json.loads((dataset/fold/"meta/info.json").read_bytes())
        if info["fps"] != 30 or any(
            info["features"][key]["names"] != list(JOINTS)
            or info["features"][key]["shape"] != [6] or info["features"][key]["dtype"] != "float32"
            for key in ("action", "observation.state")
        ):
            raise ValueError(f"X-VLA joint order or timeline differs in {fold}")
    train_stats = json.loads((dataset/"train/meta/stats.json").read_bytes())["action"]
    checksums = {}
    for processor, step_name in (("pre", "normalizer_processor"), ("post", "unnormalizer_processor")):
        definition = checkpoint/f"policy_{processor}processor.json"
        document = json.loads(definition.read_bytes())
        step, = [s for s in document["steps"] if s["registry_name"] == step_name]
        if step["config"]["norm_map"] != expected_norms or step["config"]["features"]["action"]["shape"] != [6]:
            raise ValueError(f"Unexpected X-VLA {processor}processor normalization contract")
        if processor == "pre":
            stages = {stage["registry_name"]: stage["config"] for stage in document["steps"]}
            if (stages["rename_observations_processor"]["rename_map"] not in ({}, expected_rename)
                or stages["xvla_add_domain_id"]["domain_id"] != 0
                or stages["tokenizer_processor"]["task_key"] != "task"
                or any(stages[key]["image_keys"] not in (None, list(expected_cameras))
                       for key in ("xvla_image_to_float", "xvla_imagenet_normalize"))):
                raise ValueError("Saved X-VLA image, task, or domain processor differs from training")
        stats_file = checkpoint/step["state_file"]
        saved_stats = load_file(str(stats_file), device="cpu")
        for stat in ("mean", "std"):
            actual = saved_stats["action."+stat]
            expected = torch.tensor(train_stats[stat], dtype=actual.dtype)
            if actual.shape != (6,) or not torch.isfinite(actual).all() or not torch.equal(actual, expected):
                raise ValueError(f"X-VLA {processor}processor {stat} differs from train-only statistics")
        checksums.update({definition.name: digest(definition), stats_file.name: digest(stats_file)})
    return dict(path="native_lerobot_offline", joint_order=JOINTS, camera_rename=expected_rename,
                action_normalization="MEAN_STD", state_normalization="IDENTITY", domain_id=0,
                processor_sha256=checksums), train_stats


class ReplayPolicy:
    def __init__(self, args, checkpoint, mode="chunk"):
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class, make_pre_post_processors

        self.name, self.mode = args.model, mode
        self.device = args.device
        self.adapter = None
        self.policy = None
        self.offline_contract = None
        self.weight_sha = digest(checkpoint/"model.safetensors")
        source = json.loads((checkpoint/"train_config.json").read_bytes())
        cfg = PreTrainedConfig.from_pretrained(checkpoint)
        cfg.device = self.device
        self.cfg = cfg
        self.rename = source.get("rename_map") or {}
        if args.model == HISTORICAL:
            saved = json.loads((checkpoint/"policy_preprocessor.json").read_bytes())
            step, = [s for s in saved["steps"] if s["registry_name"] == "rename_observations_processor"]
            self.rename = step["config"]["rename_map"]
        if mode == "temporal":
            if args.model not in {"act", "act_task"}:
                raise ValueError("Temporal ensembling is supported for ACT only")
            cfg.temporal_ensemble_coeff = 0.01
            cfg.n_action_steps = 1
        if args.model == "xvla" and not args.xvla_native:
            from round2.xvla_bundle import load_cpu_bundle

            if not args.bundle or not args.approved_sha or self.device != "cpu":
                raise ValueError("X-VLA requires an approved CPU bundle or --xvla-native for offline CUDA replay")
            if digest(args.bundle/"checkpoint/model.safetensors") != self.weight_sha:
                raise ValueError("X-VLA bundle weights differ from the final checkpoint")
            self.adapter = load_cpu_bundle(args.bundle, args.approved_sha)
            return
        if args.xvla_native:
            if args.model != "xvla" or args.device != "cuda" or args.bundle or args.approved_sha:
                raise ValueError("--xvla-native is for CUDA X-VLA replay without a deployment bundle")
            self.offline_contract, native_stats = verify_native_xvla(args.dataset, checkpoint, cfg, self.rename)
        self.policy = get_policy_class(cfg.type).from_pretrained(checkpoint, config=cfg).to(self.device).eval()
        self.pre, self.post = make_pre_post_processors(
            cfg, str(checkpoint),
            preprocessor_overrides={"device_processor": {"device": self.device},
                                    "rename_observations_processor": {"rename_map": self.rename}},
            postprocessor_overrides={"device_processor": {"device": "cpu"}},
        )

        if self.offline_contract:
            mean = torch.tensor(native_stats["mean"], dtype=torch.float32)
            std = torch.tensor(native_stats["std"], dtype=torch.float32)
            probe = torch.stack((torch.zeros_like(mean), torch.ones_like(mean))).unsqueeze(0).to(self.device)
            decoded = self.post(probe).cpu()
            torch.testing.assert_close(decoded, torch.stack((mean, mean+std)).unsqueeze(0))
            self.offline_contract["unnormalizer_zero_and_one_probe"] = "matched_train_stats"

    def reset(self):
        if self.policy is not None:
            self.policy.reset()
            self.pre.reset()
            self.post.reset()

    def __call__(self, item, color):
        task = f"Put medicine bottle A into the {color} basket"
        if self.adapter:
            from xvla_adapter import JOINTS, RGBFrame, VIEWS

            imgs = {}
            for view, key in zip(VIEWS, RAW_VIEWS, strict=True):
                rgb = item["observation.images."+key]
                if rgb.dtype != torch.uint8:
                    rgb = (rgb*255).round().to(torch.uint8)
                rgb = rgb.reshape(-1, *rgb.shape[-3:])[-1].permute(1,2,0).contiguous().numpy()
                imgs[view] = RGBFrame(rgb.shape[1], rgb.shape[0], rgb.tobytes())
            state = dict(zip(JOINTS, item["observation.state"].reshape(-1,6)[-1].tolist(), strict=True))
            obs = self.adapter.prepare_observation(views=imgs, state=state, task=task)
            return np.asarray(self.adapter.decode_actions(self.adapter.predict(obs)))
        obs = {key: value for key, value in item.items()
               if key.startswith("observation.") and not key.endswith("_is_pad")}
        if self.name == "act_task":
            state = obs["observation.state"].clone()
            if state.shape[-1] != 9:
                raise ValueError("ACT + task expects 6 joints and 3 one-hot values")
            state[..., -3:] = state.new_tensor([float(c == color) for c in COLORS])
            obs["observation.state"] = state
        for key, val in obs.items():
            if key.startswith("observation.images.") and val.dtype == torch.uint8:
                obs[key] = val.float()/255
        obs["task"] = task
        batch = self.pre(default_collate([obs]))
        ctx = torch.autocast("cuda") if self.cfg.use_amp and self.device == "cuda" else nullcontext()
        with ctx:
            if self.mode == "temporal":
                result = self.policy.select_action(batch).unsqueeze(1)
            else:
                result = self.policy.predict_action_chunk(batch)
        return self.post(result.float())[0].detach().cpu().float().numpy()


def observations(root, replay, video_backend="pyav"):
    from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
    from lerobot.datasets.factory import resolve_delta_timestamps
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    meta = LeRobotDatasetMetadata("local/model-compare-eval", root=root/"eval")
    deltas = resolve_delta_timestamps(replay.cfg, meta, replay.rename) or {}
    deltas = {key: val for key, val in deltas.items() if key.startswith("observation.")}
    return LeRobotDataset("local/model-compare-eval", root=root/"eval", video_backend=video_backend,
                          delta_timestamps=deltas or None, return_uint8=True)


def predict(replay, item, color, episode, frame, count):
    replay.reset()
    # Pair the stochastic initialization across all prompts for this observation.
    torch.manual_seed(101 + 100000 * episode + frame)
    chunk = replay(item, color)
    if chunk.ndim != 2 or chunk.shape[0] < count or chunk.shape[1] != 6 or not np.isfinite(chunk).all():
        raise ValueError("Expected a finite 6D action chunk")
    return chunk[:count]


def evaluate(args):
    split, checkpoint, step, target = checkpoint_for(args)
    root = args.dataset
    replay = ReplayPolicy(args, checkpoint)
    horizon = replay_horizon(replay.cfg)
    dataset = observations(root, replay)
    rows = [r for r in split["episodes"] if r["fold"] == "eval"]
    if len(dataset) != sum(r["frames"] for r in rows) or len(rows) != 21:
        raise ValueError("Incorrect replay fold")
    if not args.skip_prompt_check:
        truth = load_fold(root, split, "eval")
        train = load_fold(root, split, "train")
        scale = np.maximum(np.concatenate([a[:, :5] for _, a in train.values()]).std(0), 1e-6)
        refs = []
        for label, actions in train.values():
            segment = placement(actions, release(actions, closing(actions)), scale)
            if segment is not None:
                refs.append((label, segment))
        if {label for label, _ in refs} != set(COLORS):
            raise ValueError("Missing placement reference color")
    args.output.mkdir(parents=True, exist_ok=True)
    historical = args.model == HISTORICAL
    provenance = dict(model=args.model, weight_sha256=replay.weight_sha, historical=historical,
                      split_sha256=digest(root/"split.json"),
                      heldout_for_this_model=not historical, open_loop_horizon=horizon,
                      checkpoint_step=step, target_steps=target)
    if replay.offline_contract:
        provenance["xvla_offline_contract"] = replay.offline_contract
    start, swapped = 0, []
    with torch.inference_mode():
        for episode_index, row in enumerate(rows):
            episode, color, length = row["id"], row["color"], row["frames"]
            event = None
            if not args.skip_prompt_check:
                action = truth[episode][1]
                event = release(action, closing(action))
            eligible = event is not None and event >= FPS
            pairs = {} if args.skip_prompt_check else {c: [] for c in COLORS if c != color}
            predictions, original_window = [], []
            for frame in range(0, length, horizon):
                take = min(horizon, length-frame)
                intersects = eligible and frame < event and frame+take > event-FPS
                if args.prompt_only and not intersects:
                    continue
                item = dataset[start+frame]
                if int(item["episode_index"]) != episode_index or int(item["frame_index"]) != frame:
                    raise ValueError("Replay observation and reference action are misaligned")
                original = predict(replay, item, color, episode, frame, take)
                if not args.prompt_only:
                    predictions.append(original)
                if intersects:
                    lo, hi = max(0, event-FPS-frame), min(take, event-frame)
                    original_window.append(original[lo:hi])
                    for requested in pairs:
                        other = predict(replay, item, requested, episode, frame, take)
                        pairs[requested].append(other[lo:hi])
            for requested, segments in pairs.items():
                nearest, change = None, None
                if segments:
                    swapped_window = np.concatenate(segments)
                    normalized = swapped_window[:, :5] / scale
                    if len(normalized) != FPS:
                        raise ValueError("Incomplete placement segment")
                    nearest = min(refs, key=lambda ref: np.square(ref[1]-normalized).mean())[0]
                    change = float(np.abs(swapped_window[:, :5]-np.concatenate(original_window)[:, :5]).mean())
                swapped.append(dict(episode=episode, recorded=color, requested=requested,
                                    nearest=nearest, arm_change_mae_deg=change))
            if not args.prompt_only:
                np.save(args.output/f"episode_{episode}.npy", np.concatenate(predictions), allow_pickle=False)
            start += length
            print(f"{args.model} step {step}: episode {episode_index+1}/{len(rows)} (id {episode})", flush=True)
    if not args.skip_prompt_check:
        if len(swapped) != 42:
            raise ValueError("Expected 42 swapped prompts")
        write(args.output/"prompt_check.json", dict(provenance,
              matches=sum(r["requested"] == r["nearest"] for r in swapped), n=len(swapped),
              eligible=sum(r["nearest"] is not None for r in swapped), rows=swapped))
    if not args.prompt_only:
        write(args.output/"metrics.json", dict(provenance, **score(root, args.output)))
        write(args.output/"metrics_blue.json", dict(provenance, **score(root, args.output, "blue")))


def add_policy_arguments(parser):
    parser.add_argument("model", choices=[*SCHEDULE, HISTORICAL])
    parser.add_argument("dataset", type=Path)
    parser.add_argument("runs", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--step", type=int, help="Queue-verified official checkpoint step; default is latest")
    parser.add_argument("--checkpoint", type=Path, help="Explicit historical SmolVLA checkpoint")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--approved-sha")
    parser.add_argument("--xvla-native", action="store_true", help="Use the verified training processor path for offline CUDA X-VLA replay")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    add_policy_arguments(p)
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--prompt-only", action="store_true")
    scope.add_argument("--skip-prompt-check", action="store_true")
    evaluate(p.parse_args())
