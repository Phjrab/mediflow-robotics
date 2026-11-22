"""One measured ACT update on a real train episode. No checkpoint or ranking."""
import argparse
import json
import time
from pathlib import Path

import av
import pyarrow.parquet as pq
import torch
from lerobot.configs.types import FeatureType, PolicyFeature
from lerobot.policies.act.configuration_act import ACTConfig
from lerobot.policies.act.modeling_act import ACTPolicy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.dataset.name != "train":
        raise ValueError("Expected train fold")
    torch.manual_seed(101)
    torch.set_num_threads(4)
    info = json.loads((args.dataset / "meta/info.json").read_bytes())
    meta = pq.ParquetFile(args.dataset / "meta/episodes/chunk-000/file-000.parquet").read().to_pylist()[0]
    assert meta["length"] >= 100 and meta["dataset_from_index"] == 0
    table = pq.ParquetFile(args.dataset / "data/chunk-000/file-000.parquet").read().slice(0, 100)
    item = {"action": torch.tensor(table["action"].to_pylist()),
            "observation.state": torch.tensor(table["observation.state"][0].as_py()),
            "action_is_pad": torch.zeros(100, dtype=torch.bool)}
    cameras = [k for k, v in info["features"].items() if v["dtype"] == "video"]
    features = {"observation.state": PolicyFeature(FeatureType.STATE, (6,))}
    batch = {"observation.state": item["observation.state"][None]}
    mean = torch.tensor([0.485, 0.456, 0.406])[:, None, None]
    std = torch.tensor([0.229, 0.224, 0.225])[:, None, None]
    for camera in cameras:
        prefix = f"videos/{camera}/"
        path = info["video_path"].format(video_key=camera, chunk_index=meta[prefix+"chunk_index"],
                                         file_index=meta[prefix+"file_index"])
        with av.open(str(args.dataset / path)) as video:
            timestamp = meta[prefix+"from_timestamp"]
            stream = video.streams.video[0]
            video.seek(int(timestamp / stream.time_base), stream=stream)
            frame = next(f for f in video.decode(stream) if f.time >= timestamp - 0.5 / 30)
            assert abs(frame.time - timestamp) <= 0.5 / 30
            rgb = torch.from_numpy(frame.to_ndarray(format="rgb24")).permute(2, 0, 1)
        if rgb.dtype == torch.uint8:
            rgb = rgb.float() / 255
        features[camera] = PolicyFeature(FeatureType.VISUAL, tuple(rgb.shape))
        batch[camera] = ((rgb - mean) / std)[None]
    cfg = ACTConfig(input_features=features, output_features={"action": PolicyFeature(FeatureType.ACTION, (6,))},
                    device="cpu", pretrained_backbone_weights=None)
    policy = ACTPolicy(cfg)
    opt = torch.optim.AdamW(policy.parameters(), lr=cfg.optimizer_lr)
    batch["action"] = item["action"][None]
    for k in ("observation.state", "action"):
        stats = json.loads((args.dataset / "meta/stats.json").read_bytes())[k]
        batch[k] = (batch[k] - torch.as_tensor(stats["mean"])) / torch.as_tensor(stats["std"]).clamp_min(1e-8)
    batch["action_is_pad"] = item["action_is_pad"][None]
    start = time.perf_counter()
    loss, _ = policy(batch)
    loss.backward()
    opt.step()
    assert torch.isfinite(loss)
    result = dict(model="ACT", seed=101, batch=1, steps=1, wall_update_s=time.perf_counter()-start,
                  parameters=sum(p.numel() for p in policy.parameters()), train_example=0,
                  cameras=len(cameras), backbone="random; offline feasibility probe", loss=float(loss.detach()))
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
