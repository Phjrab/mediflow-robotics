"""GPU holdout metrics with train-fitted processors and no holdout statistics."""
import math
import time


class DeadlineReached(Exception):
    """The remaining wall time is insufficient to finish the current operation."""


def validate(cfg, policy, pre, post, accelerator, root, split, deadline):
    import numpy as np
    import torch
    from torch.utils.data import DataLoader

    from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
    from lerobot.datasets.factory import resolve_delta_timestamps
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.scripts.lerobot_train import _preprocess_dataset_batch
    from lerobot.utils.collate import lerobot_collate_fn

    from metrics import load_fold

    def check_time():
        if time.monotonic() >= deadline:
            raise DeadlineReached

    def preprocess(raw):
        return _preprocess_dataset_batch(raw, metadata.camera_keys, cfg.rename_map, pre)

    rows = [row for row in split["episodes"] if row["fold"] == "eval"]
    truth = load_fold(root, split, "eval")
    repo_id = "local/model-compare-eval"
    metadata = LeRobotDatasetMetadata(repo_id, root=root/"eval")
    dataset = LeRobotDataset(repo_id, root=root/"eval", video_backend="pyav",
                            delta_timestamps=resolve_delta_timestamps(policy.config, metadata, cfg.rename_map))
    frames = sum(row["frames"] for row in rows)
    if len(rows) != 21 or metadata.total_episodes != 21 or len(dataset) != frames:
        raise ValueError("Unexpected validation fold size")
    device = accelerator.device
    loss_sum = 0.0
    errors = np.zeros(6, dtype=np.float64)
    # Every validation evaluates the same frames, with reproducible stochastic
    # losses and denoising. Restore the training RNG stream afterwards.
    policy.eval()
    try:
        with torch.random.fork_rng(devices=[device.index or 0]), torch.inference_mode(), accelerator.autocast():
            torch.manual_seed(101)
            loader = DataLoader(dataset, batch_size=cfg.batch_size, num_workers=cfg.num_workers,
                                shuffle=False, drop_last=False, collate_fn=lerobot_collate_fn)
            observed = 0
            for raw in loader:
                check_time()
                count = len(raw["index"])
                batch = preprocess(raw)
                loss, _ = policy(batch)
                loss_sum += float(loss) * count
                observed += count
            if observed != frames:
                raise ValueError("Validation loss did not cover all holdout frames")

            start = 0
            stride = min(16, policy.config.n_action_steps)
            for episode_index, row in enumerate(rows):
                actual = truth[row["id"]][1]
                for offset in range(0, len(actual), stride):
                    check_time()
                    item = dataset[start + offset]
                    if int(item["episode_index"]) != episode_index or int(item["frame_index"]) != offset:
                        raise ValueError("Holdout observations and labels are misaligned")
                    # Keep preprocessing state for policies with relative actions.
                    # Never pass ground-truth actions to the inference call.
                    obs = {key: value for key, value in item.items()
                           if key.startswith("observation.") and not key.endswith("_is_pad")}
                    obs["task"] = item["task"]
                    for component in (policy, pre, post):
                        component.reset()
                    predicted = policy.predict_action_chunk(preprocess(lerobot_collate_fn([obs])))
                    decoded = post(predicted.float()).detach().cpu().numpy()
                    take = min(stride, len(actual) - offset)
                    if (decoded.ndim != 3 or decoded.shape[0] != 1 or decoded.shape[1] < take
                            or decoded.shape[2] != 6 or not np.isfinite(decoded).all()):
                        raise ValueError("Policy did not decode a finite six-axis action chunk")
                    errors += np.abs(decoded[0, :take] - actual[offset:offset + take]).sum(axis=0)
                start += len(actual)
    finally:
        policy.reset()
        pre.reset()
        post.reset()
        policy.train()
    loss = loss_sum / frames
    if not math.isfinite(loss) or not np.isfinite(errors).all():
        raise ValueError("Non-finite holdout metrics")
    # Dataset units are absolute joint degrees and gripper points on a 0–100 scale.
    return [loss, len(rows), frames, *(errors / frames)]
