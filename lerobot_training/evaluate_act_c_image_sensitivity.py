#!/usr/bin/env python3
"""Measure whether ACT-C changes its action chunk when camera images change.

This is an offline-only diagnostic: it never imports robot drivers, opens a
serial port, or sends motor commands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from PIL import Image

from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference


CAMERAS = ("ceiling_vertical", "ceiling_oblique", "end_effector")
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
TASK = "Pick up medicine bottle C and place it into basket C."


def first_video_frame(path: Path) -> np.ndarray:
    with av.open(str(path)) as container:
        return np.asarray(next(container.decode(video=0)).to_image(), dtype=np.uint8)


def load_training_start(dataset: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    tables = [
        pq.read_table(p, columns=["episode_index", "frame_index", "observation.state"])
        for p in sorted((dataset / "data").rglob("*.parquet"))
    ]
    rows = pa.concat_tables(tables).to_pylist()
    starts = np.asarray([row["observation.state"] for row in rows if row["frame_index"] == 0], dtype=np.float32)
    state = np.median(starts, axis=0)
    images = {
        name: first_video_frame(
            dataset / "videos" / f"observation.images.{name}" / "chunk-000" / "file-000.mp4"
        )
        for name in CAMERAS
    }
    return state, images


def load_images(root: Path) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(Image.open(root / f"{name}.jpg").convert("RGB"), dtype=np.uint8)
        for name in CAMERAS
    }


def predict(policy, preprocessor, postprocessor, images, state, device) -> np.ndarray:
    observation = {"observation.state": state.astype(np.float32)}
    for name in CAMERAS:
        observation[f"observation.images.{name}"] = images[name]
    batch = prepare_observation_for_inference(observation, device, TASK, "so_follower")
    batch = preprocessor(batch)
    with torch.inference_mode():
        chunk = postprocessor(policy.predict_action_chunk(batch))
    return chunk.squeeze(0).detach().cpu().numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--current-images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    np.random.seed(0)
    torch.manual_seed(0)
    device = torch.device(args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu")

    state, training_images = load_training_start(args.dataset)
    current = load_images(args.current_images)
    black = {name: np.zeros_like(image) for name, image in current.items()}
    mean_color = {
        name: np.broadcast_to(image.mean(axis=(0, 1), keepdims=True).astype(np.uint8), image.shape).copy()
        for name, image in current.items()
    }
    scenarios = {
        "current": current,
        "training_episode0": training_images,
        "all_black": black,
        "all_mean_color": mean_color,
        "swap_vertical_oblique": {
            "ceiling_vertical": current["ceiling_oblique"],
            "ceiling_oblique": current["ceiling_vertical"],
            "end_effector": current["end_effector"],
        },
    }
    for camera in CAMERAS:
        ablated = dict(current)
        ablated[camera] = black[camera]
        scenarios[f"black_{camera}"] = ablated

    config = PreTrainedConfig.from_pretrained(args.model)
    policy_cls = get_policy_class(config.type)
    policy = policy_cls.from_pretrained(args.model, config=config).to(device).eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=config,
        pretrained_path=str(args.model),
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )

    chunks = {
        name: predict(policy, preprocessor, postprocessor, images, state, device)
        for name, images in scenarios.items()
    }
    reference = chunks["current"]
    results = {}
    for name, chunk in chunks.items():
        delta = chunk - reference
        results[name] = {
            "chunk_rmse_vs_current": float(np.sqrt(np.mean(delta**2))),
            "per_joint_rmse_vs_current": dict(
                zip(JOINTS, np.sqrt(np.mean(delta**2, axis=0)).round(4).tolist(), strict=True)
            ),
            "first_action": dict(zip(JOINTS, chunk[0].round(4).tolist(), strict=True)),
            "last_action": dict(zip(JOINTS, chunk[-1].round(4).tolist(), strict=True)),
        }

    # Context: how much the nominal chunk itself changes over time. Image
    # sensitivity far below this value indicates the policy mostly follows a
    # memorized state-conditioned trajectory rather than visual differences.
    nominal_motion_rmse = float(np.sqrt(np.mean((reference - reference[0]) ** 2)))
    report = {
        "device": str(device),
        "fixed_start_state": dict(zip(JOINTS, state.round(4).tolist(), strict=True)),
        "nominal_chunk_motion_rmse_from_first_action": nominal_motion_rmse,
        "scenarios": results,
    }
    (args.output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    np.savez_compressed(args.output / "chunks.npz", **chunks)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
