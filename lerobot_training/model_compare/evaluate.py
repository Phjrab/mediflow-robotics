"""Score the final official checkpoint on the frozen fold, 16 open-loop actions at a time."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import default_collate

from metrics import score
from prepare import digest, write
from train_queue import REPORT, SCHEDULE


def evaluate(args):
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
    from lerobot.datasets.factory import resolve_delta_timestamps
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.policies.factory import get_policy_class, make_pre_post_processors

    name = args.model
    provenance = json.loads((args.runs/(name+".provenance.json")).read_bytes())
    root = args.dataset
    split = json.loads((root/"split.json").read_bytes())
    if (split != json.loads((REPORT/"split.json").read_bytes()) or provenance["split_sha"] != digest(REPORT/"split.json")
        or provenance["train_stats_sha"] != digest(root/"train/meta/stats.json") or provenance["name"] != name):
        raise ValueError("Checkpoint split/stats mismatch")
    wanted = SCHEDULE[name][0]
    folders = [p for p in (args.runs/name/"checkpoints").glob("[0-9]*") if int(p.name) == wanted]
    if len(folders) != 1:
        raise ValueError(f"Expected final checkpoint {wanted}")
    checkpoint = folders[0]/"pretrained_model"
    source = json.loads((checkpoint/"train_config.json").read_bytes())
    cfg = PreTrainedConfig.from_pretrained(checkpoint)
    cfg.device = args.device
    rename = source.get("rename_map", {})
    meta = LeRobotDatasetMetadata("local/model-compare-eval", root=root/"eval")
    deltas = resolve_delta_timestamps(cfg, meta, rename) or {}
    deltas = {k:v for k,v in deltas.items() if k.startswith("observation.")}
    ds = LeRobotDataset("local/model-compare-eval", root=root/"eval", video_backend="pyav",
                        delta_timestamps=deltas or None)
    rows = [r for r in split["episodes"] if r["fold"]=="eval"]
    assert len(ds) == sum(r["frames"] for r in rows)
    if name == "xvla":
        from round2.xvla_bundle import load_cpu_bundle
        from xvla_adapter import JOINTS, RGBFrame, VIEWS
        if not args.bundle or not args.approved_sha or args.device != "cpu":
            raise ValueError("X-VLA requires the corrected round2 CPU decoder and an approved bundle")
        if digest(args.bundle/"checkpoint/model.safetensors") != digest(checkpoint/"model.safetensors"):
            raise ValueError("X-VLA bundle weights differ from the final checkpoint")
        adapter = load_cpu_bundle(args.bundle, args.approved_sha)
    else:
        policy = get_policy_class(cfg.type).from_pretrained(checkpoint, config=cfg).to(args.device).eval()
        pre, post = make_pre_post_processors(cfg, str(checkpoint),
                    preprocessor_overrides={"device_processor":{"device":args.device}},
                    postprocessor_overrides={"device_processor":{"device":"cpu"}})
    torch.manual_seed(101)
    args.output.mkdir(parents=True, exist_ok=True)
    start = 0
    with torch.inference_mode():
        for row in rows:
            pred = []
            for k in range(0, row["frames"], 16):
                item = ds[start+k]
                if name == "xvla":
                    imgs = {}
                    for view, key in zip(VIEWS, ("end_effector","ceiling_vertical","ceiling_oblique"), strict=True):
                        rgb = item["observation.images."+key]
                        if rgb.dtype != torch.uint8:
                            rgb = (rgb*255).round().to(torch.uint8)
                        rgb = rgb.reshape(-1,*rgb.shape[-3:])[-1].permute(1,2,0).contiguous().numpy()
                        imgs[view] = RGBFrame(rgb.shape[1], rgb.shape[0], rgb.tobytes())
                    state = dict(zip(JOINTS, item["observation.state"].reshape(-1,6)[-1].tolist(), strict=True))
                    obs = adapter.prepare_observation(views=imgs,state=state,
                                                      task=item["task"])
                    chunk = np.asarray(adapter.decode_actions(adapter.predict(obs)))
                else:
                    policy.reset()
                    obs = {rename.get(key,key): val for key,val in item.items()
                           if key.startswith("observation.") and not key.endswith("_is_pad")}
                    for key,val in list(obs.items()):
                        if val.dtype == torch.uint8:
                            obs[key] = val.float()/255
                    obs["task"] = item["task"]
                    batch = pre(default_collate([obs]))
                    ctx = torch.autocast("cuda") if cfg.use_amp and args.device=="cuda" else nullcontext()
                    with ctx:
                        out = policy.predict_action_chunk(batch)
                    chunk = post(out)[0].cpu().float().numpy()
                take = min(16, row["frames"]-k)
                if chunk.ndim != 2 or chunk.shape[0] < take or chunk.shape[1] != 6:
                    raise ValueError("Expected a full 6D chunk")
                pred.extend(chunk[:take])
            np.save(args.output/f"episode_{row['id']}.npy", np.asarray(pred), allow_pickle=False)
            start += row["frames"]
    write(args.output/"metrics.json", score(root, args.output))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("model", choices=list(SCHEDULE))
    p.add_argument("dataset", type=Path)
    p.add_argument("runs", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--device", choices=["cuda","cpu"], default="cuda")
    p.add_argument("--bundle", type=Path)
    p.add_argument("--approved-sha")
    evaluate(p.parse_args())
