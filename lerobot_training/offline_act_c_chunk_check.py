#!/usr/bin/env python3
"""Inspect ACT action chunks without connecting to or commanding the robot."""

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
JOINTS = ("pan", "lift", "elbow", "wrist_flex", "wrist_roll", "gripper")
TASK = "Pick up medicine bottle C and place it into basket C."


def video_frame(path: Path, frame_index: int) -> np.ndarray:
    with av.open(str(path)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index == frame_index:
                return np.asarray(frame.to_image(), dtype=np.uint8)
    raise IndexError(f"video has no frame {frame_index}: {path}")


def load_episode_zero(dataset: Path, frame_index: int) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    tables = [
        pq.read_table(p, columns=["episode_index", "observation.state", "action"])
        for p in sorted((dataset / "data").rglob("*.parquet"))
    ]
    table = pa.concat_tables(tables).to_pydict()
    indices = [i for i, episode in enumerate(table["episode_index"]) if episode == 0]
    states = np.asarray([table["observation.state"][i] for i in indices], dtype=np.float32)
    actions = np.asarray([table["action"][i] for i in indices], dtype=np.float32)
    if not 0 <= frame_index < len(states):
        raise ValueError(f"reference frame {frame_index} is outside episode 0 (length {len(states)})")
    images = {
        name: video_frame(
            dataset / "videos" / f"observation.images.{name}" / "chunk-000" / "file-000.mp4",
            frame_index,
        )
        for name in CAMERAS
    }
    return states[frame_index:], actions[frame_index:], images


def load_current_images(root: Path) -> dict[str, np.ndarray]:
    return {name: np.asarray(Image.open(root / f"{name}.jpg").convert("RGB"), dtype=np.uint8) for name in CAMERAS}


def predict_chunk(policy, preprocessor, postprocessor, images, state, device, task) -> np.ndarray:
    observation = {"observation.state": np.asarray(state, dtype=np.float32)}
    for name, image in images.items():
        observation[f"observation.images.{name}"] = image
    batch = prepare_observation_for_inference(observation, device, task, "so_follower")
    batch = preprocessor(batch)
    with torch.inference_mode():
        chunk = policy.predict_action_chunk(batch)
        chunk = postprocessor(chunk)
    return chunk.squeeze(0).detach().cpu().numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--current-images", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--runtime-state", type=float, nargs=6)
    parser.add_argument("--training-start-mean", type=float, nargs=6)
    parser.add_argument("--label", default="ACT-C")
    parser.add_argument("--reference-frame", type=int, default=0, help="Frame within episode 0 (30 FPS)")
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    np.random.seed(0)
    torch.manual_seed(0)

    device = torch.device(args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu")
    states, actions, training_images = load_episode_zero(args.dataset, args.reference_frame)
    current_images = load_current_images(args.current_images) if args.current_images else None

    config = PreTrainedConfig.from_pretrained(args.model)
    policy_class = get_policy_class(config.type)
    policy = policy_class.from_pretrained(args.model, config=config).to(device).eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=config,
        pretrained_path=str(args.model),
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )

    inferred_runtime_state = np.asarray(
        args.runtime_state or [-10.5934, -96.8352, 96.5714, 57.7143, -11.9, 5.8752],
        dtype=np.float32,
    )
    training_start_mean = np.asarray(
        args.training_start_mean or [-5.44, -98.53, 90.03, 70.52, -11.86, 2.82],
        dtype=np.float32,
    )
    scenarios = {
        "training_frame_and_state": predict_chunk(
            policy, preprocessor, postprocessor, training_images, states[0], device, args.task
        ),
    }
    if current_images is not None:
        scenarios["current_images_training_mean_state"] = predict_chunk(
            policy, preprocessor, postprocessor, current_images, training_start_mean, device, args.task
        )
        scenarios["current_images_inferred_runtime_state"] = predict_chunk(
            policy, preprocessor, postprocessor, current_images, inferred_runtime_state, device, args.task
        )

    horizon = min(config.n_action_steps, len(actions), *(len(v) for v in scenarios.values()))
    reference = actions[:horizon]
    metrics: dict[str, dict[str, object]] = {}
    for name, chunk in scenarios.items():
        chunk = chunk[:horizon]
        metrics[name] = {
            "first_action": chunk[0].round(4).tolist(),
            "last_action": chunk[-1].round(4).tolist(),
            "per_joint_min": chunk.min(axis=0).round(4).tolist(),
            "per_joint_max": chunk.max(axis=0).round(4).tolist(),
            "max_consecutive_step": np.abs(np.diff(chunk, axis=0)).max(axis=0).round(4).tolist(),
            "rmse_vs_episode0_reference_chunk": float(np.sqrt(np.mean((chunk - reference) ** 2))),
        }

    (args.output / "metrics.json").write_text(
        json.dumps(
            {
                "device": str(device),
                "horizon": horizon,
                "training_episode0_frame_index": args.reference_frame,
                "training_episode0_frame_state": states[0].round(4).tolist(),
                "training_start_mean": training_start_mean.tolist(),
                "inferred_runtime_state": inferred_runtime_state.tolist(),
                "task": args.task,
                "scenarios": metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )

    np.savez_compressed(
        args.output / "chunks.npz",
        reference=reference,
        **{name: chunk[:horizon] for name, chunk in scenarios.items()},
    )

    if args.plot:
        try:
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(3, 2, figsize=(14, 12), sharex=True)
            x = np.arange(horizon) / 30.0
            for joint, ax in enumerate(axes.ravel()):
                ax.plot(x, reference[:, joint], color="black", linewidth=2, label="episode 0 recorded action")
                for name, chunk in scenarios.items():
                    ax.plot(x, chunk[:horizon, joint], linewidth=1.4, label=name)
                ax.set_title(JOINTS[joint])
                ax.set_ylabel("degrees / normalized gripper")
                ax.grid(alpha=0.25)
            axes[-1, 0].set_xlabel("seconds")
            axes[-1, 1].set_xlabel("seconds")
            handles, labels = axes[0, 0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="lower center", ncol=2)
            fig.suptitle(f"{args.label} offline first action chunk (no robot commands)")
            fig.tight_layout(rect=(0, 0.08, 1, 0.97))
            fig.savefig(args.output / "chunk_comparison.png", dpi=160)
        except ImportError as exc:
            print(f"plot skipped: {exc}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
