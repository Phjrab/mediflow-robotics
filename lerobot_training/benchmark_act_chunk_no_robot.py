#!/usr/bin/env python3
"""Time ACT chunk prediction with synthetic observations; never opens robot hardware."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--runs", type=int, default=6)
    args = parser.parse_args()

    device = torch.device(args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu")
    config = PreTrainedConfig.from_pretrained(args.model)
    policy = get_policy_class(config.type).from_pretrained(args.model, config=config).to(device).eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=config,
        pretrained_path=str(args.model),
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    observation = {
        "observation.state": np.array([-10.242, -98.593, 96.571, 48.396, -4.527, 5.814], dtype=np.float32),
        "observation.images.ceiling_vertical": frame,
        "observation.images.ceiling_oblique": frame,
        "observation.images.end_effector": frame,
    }
    batch = prepare_observation_for_inference(
        observation,
        device,
        "Pick up medicine bottle A and place it into basket A.",
        "so_follower",
    )
    batch = preprocessor(batch)

    durations = []
    with torch.inference_mode():
        for _ in range(args.runs):
            started = time.perf_counter()
            chunk = postprocessor(policy.predict_action_chunk(batch))
            if device.type == "cuda":
                torch.cuda.synchronize()
            durations.append(time.perf_counter() - started)

    print(json.dumps({
        "device": str(device),
        "runs_s": [round(value, 4) for value in durations],
        "warm_median_s": round(float(np.median(durations[1:])), 4) if len(durations) > 1 else None,
        "chunk_shape": list(chunk.shape),
    }))


if __name__ == "__main__":
    main()
