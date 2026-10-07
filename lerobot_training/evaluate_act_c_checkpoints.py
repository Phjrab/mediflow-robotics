#!/usr/bin/env python3
"""Rank ACT-C checkpoints offline using recorded demonstrations and current camera images.

This script never connects to a robot and never sends motor commands.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
from pathlib import Path

import av
import numpy as np
import pyarrow.parquet as pq
import torch
from PIL import Image

from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.policies.utils import prepare_observation_for_inference


CAMERAS = ("ceiling_vertical", "ceiling_oblique", "end_effector")
TASK = "Pick up medicine bottle C and place it into basket C."
FPS = 30.0


def frame_at(path: Path, timestamp: float) -> np.ndarray:
    """Decode the first frame at (or immediately before) a timestamp."""
    with av.open(str(path)) as container:
        container.seek(max(0, int(timestamp * av.time_base)), backward=True)
        previous = None
        for frame in container.decode(video=0):
            image = np.asarray(frame.to_image(), dtype=np.uint8).copy()
            frame_time = float(frame.time or 0.0)
            if frame_time >= timestamp - (0.5 / FPS):
                return image
            previous = image
        if previous is not None:
            return previous
    raise RuntimeError(f"No video frame decoded from {path} at {timestamp:.3f}s")


def load_samples(dataset: Path, episode_ids: list[int], offsets: list[int]):
    data = pq.read_table(
        dataset / "data/chunk-000/file-000.parquet",
        columns=["episode_index", "frame_index", "observation.state", "action"],
    ).to_pydict()
    episodes = {
        int(row["episode_index"]): row
        for row in pq.read_table(dataset / "meta/episodes/chunk-000/file-000.parquet").to_pylist()
    }
    rows_by_episode: dict[int, list[int]] = {}
    for row_index, episode_index in enumerate(data["episode_index"]):
        rows_by_episode.setdefault(int(episode_index), []).append(row_index)

    samples = []
    for episode_id in episode_ids:
        episode = episodes[episode_id]
        episode_rows = rows_by_episode[episode_id]
        for offset in offsets:
            if offset + 100 > len(episode_rows):
                continue
            row = episode_rows[offset]
            images = {}
            for camera in CAMERAS:
                prefix = f"videos/observation.images.{camera}"
                file_index = int(episode[f"{prefix}/file_index"])
                timestamp = float(episode[f"{prefix}/from_timestamp"]) + offset / FPS
                video = dataset / prefix / "chunk-000" / f"file-{file_index:03d}.mp4"
                images[camera] = frame_at(video, timestamp)
            action_rows = episode_rows[offset : offset + 100]
            samples.append(
                {
                    "episode": episode_id,
                    "offset": offset,
                    "state": np.asarray(data["observation.state"][row], dtype=np.float32),
                    "actions": np.asarray([data["action"][i] for i in action_rows], dtype=np.float32),
                    "images": images,
                }
            )
    return samples


def load_current_images(root: Path) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(Image.open(root / f"{name}.jpg").convert("RGB"), dtype=np.uint8).copy()
        for name in CAMERAS
    }


def predict(policy, preprocessor, postprocessor, images, state, device) -> np.ndarray:
    observation = {"observation.state": np.asarray(state, dtype=np.float32)}
    for name, image in images.items():
        observation[f"observation.images.{name}"] = image
    batch = prepare_observation_for_inference(observation, device, TASK, "so_follower")
    batch = preprocessor(batch)
    with torch.inference_mode():
        chunk = postprocessor(policy.predict_action_chunk(batch))
    return chunk.squeeze(0).detach().cpu().numpy()


def summarize_validation(predictions: list[np.ndarray], samples: list[dict]) -> dict[str, float]:
    rmses = []
    first_errors = []
    state_jumps = []
    consecutive = []
    clipped_fraction = []
    for chunk, sample in zip(predictions, samples, strict=True):
        horizon = min(len(chunk), len(sample["actions"]))
        pred = chunk[:horizon]
        truth = sample["actions"][:horizon]
        rmses.append(float(np.sqrt(np.mean((pred - truth) ** 2))))
        first_errors.append(float(np.sqrt(np.mean((pred[0] - truth[0]) ** 2))))
        state_jumps.append(float(np.max(np.abs(pred[0] - sample["state"]))))
        diffs = np.abs(np.diff(pred, axis=0))
        consecutive.append(float(np.max(diffs)))
        clipped_fraction.append(float(np.mean(diffs > 3.0)))
    return {
        "validation_rmse_mean": float(np.mean(rmses)),
        "validation_rmse_median": float(np.median(rmses)),
        "validation_first_action_rmse_mean": float(np.mean(first_errors)),
        "validation_initial_state_jump_max": float(np.max(state_jumps)),
        "validation_consecutive_step_max": float(np.max(consecutive)),
        "validation_step_over_3deg_fraction": float(np.mean(clipped_fraction)),
    }


def summarize_current(chunk: np.ndarray, state: np.ndarray) -> dict[str, object]:
    diffs = np.abs(np.diff(chunk, axis=0))
    return {
        "first_action": chunk[0].round(3).tolist(),
        "last_action": chunk[-1].round(3).tolist(),
        "initial_state_jump_max": float(np.max(np.abs(chunk[0] - state))),
        "consecutive_step_max": float(np.max(diffs)),
        "step_over_3deg_fraction": float(np.mean(diffs > 3.0)),
        "per_joint_range": np.ptp(chunk, axis=0).round(3).tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--current-images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--episodes", default="0,10,20,30,40,50")
    parser.add_argument("--offsets", default="0,60,120")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    np.random.seed(0)
    torch.manual_seed(0)
    args.output.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu")
    episode_ids = [int(value) for value in args.episodes.split(",")]
    offsets = [int(value) for value in args.offsets.split(",")]
    samples = load_samples(args.dataset, episode_ids, offsets)
    current_images = load_current_images(args.current_images)
    runtime_state = np.asarray([-10.5934, -96.8352, 96.5714, 57.7143, -11.9, 5.8752], dtype=np.float32)

    checkpoint_models = sorted(
        model
        for model in args.checkpoints.glob("*/pretrained_model")
        if model.parent.name.isdigit()
    )
    results = []
    for model in checkpoint_models:
        step = int(model.parent.name)
        print(f"evaluating checkpoint {step:06d} on {len(samples)} recorded states", flush=True)
        config = PreTrainedConfig.from_pretrained(model)
        policy_class = get_policy_class(config.type)
        policy = policy_class.from_pretrained(model, config=config).to(device).eval()
        preprocessor, postprocessor = make_pre_post_processors(
            policy_cfg=config,
            pretrained_path=str(model),
            preprocessor_overrides={"device_processor": {"device": str(device)}},
        )
        predictions = [
            predict(policy, preprocessor, postprocessor, sample["images"], sample["state"], device)
            for sample in samples
        ]
        current_chunk = predict(
            policy, preprocessor, postprocessor, current_images, runtime_state, device
        )
        result = {
            "step": step,
            **summarize_validation(predictions, samples),
            "current_scene": summarize_current(current_chunk, runtime_state),
        }
        results.append(result)
        del policy, preprocessor, postprocessor, predictions, current_chunk
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()

    ranked = sorted(results, key=lambda item: item["validation_rmse_mean"])
    for rank, item in enumerate(ranked, start=1):
        item["rank_by_validation_rmse"] = rank
    payload = {
        "device": str(device),
        "episodes": episode_ids,
        "offsets": offsets,
        "sample_count": len(samples),
        "results_by_step": sorted(ranked, key=lambda item: item["step"]),
        "recommended_step_by_offline_rmse": ranked[0]["step"],
    }
    (args.output / "checkpoint_evaluation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )

    columns = [
        "step",
        "rank_by_validation_rmse",
        "validation_rmse_mean",
        "validation_rmse_median",
        "validation_first_action_rmse_mean",
        "validation_initial_state_jump_max",
        "validation_consecutive_step_max",
        "validation_step_over_3deg_fraction",
        "current_initial_state_jump_max",
        "current_consecutive_step_max",
        "current_step_over_3deg_fraction",
    ]
    with (args.output / "checkpoint_evaluation.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in sorted(ranked, key=lambda value: value["step"]):
            row = {key: item.get(key) for key in columns}
            row["current_initial_state_jump_max"] = item["current_scene"]["initial_state_jump_max"]
            row["current_consecutive_step_max"] = item["current_scene"]["consecutive_step_max"]
            row["current_step_over_3deg_fraction"] = item["current_scene"]["step_over_3deg_fraction"]
            writer.writerow(row)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
