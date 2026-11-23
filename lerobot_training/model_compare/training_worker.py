"""Instrument the pinned LeRobot single-GPU trainer without changing its update rule."""
import argparse
import csv
from importlib.metadata import version
import json
from pathlib import Path
import signal
import sys
import tempfile
import time

from training_validation import DeadlineReached, validate

TRAIN_FIELDS = ("step", "loss", "lr", "grad_norm", "seconds", "allocated_bytes", "reserved_bytes")
VAL_FIELDS = ("step", "val_loss", "episodes", "frames", "shoulder_pan_mae_deg",
              "shoulder_lift_mae_deg", "elbow_flex_mae_deg", "wrist_flex_mae_deg",
              "wrist_roll_mae_deg", "gripper_mae_points")


def truncate_csv(path, initial):
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header is None:
            return
        rows = [row for row in reader if len(row) == len(header) and row[0].isdigit()
                and row[-1] and int(row[0]) <= initial]
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    temporary.replace(path)


def append_csv(path, fields, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        if not exists:
            writer.writerow(fields)
        writer.writerow(row)


def validation_recorded(path, step):
    if not path.is_file():
        return False
    with path.open(newline="", encoding="utf-8") as stream:
        return any(row.get("step") == str(step) and row.get(VAL_FIELDS[-1]) for row in csv.DictReader(stream))


def run(args, trainer_args):
    import torch
    from lerobot.scripts import lerobot_train as trainer

    if version("lerobot") != "0.6.2":
        raise RuntimeError("The single-GPU instrumentation requires LeRobot 0.6.2")
    if args.base_repo:
        from huggingface_hub import snapshot_download

        snapshot = snapshot_download(args.base_repo, revision=args.base_revision)
        trainer_args.append(f"--policy.path={snapshot}")
        if args.base_repo == "lerobot/xvla-base":
            # All three basket prompts encode to 10 tokens. The base's 1024
            # padding slots alone overflow its 512-position policy transformer.
            trainer_args.append("--policy.tokenizer_max_length=64")
    train_csv = args.metrics/"train_metrics.csv"
    validation_csv = args.metrics/"validation_metrics.csv"
    split = json.loads((args.dataset/"split.json").read_bytes())
    initial = args.initial
    for path in (train_csv, validation_csv):
        truncate_csv(path, initial)
    cfg = None
    model = None
    processors = None
    accelerator = None
    stopped = False
    last_update_end = None
    probe_rows = []
    original_validate = trainer.TrainPipelineConfig.validate
    original_policy = trainer.make_policy
    original_processors = trainer.make_pre_post_processors
    original_restore = trainer.resume_before_prepare
    original_update = trainer.update_policy
    original_save = trainer.save_checkpoint
    original_update_last = trainer.update_last_checkpoint

    def request_stop(*_):
        nonlocal stopped
        stopped = True

    def setup(config):
        nonlocal cfg
        original_validate(config)
        if config.accelerator.gradient_accumulation.steps != 1:
            raise ValueError("Queue metrics require one optimizer update per step")
        cfg = config

    def make_policy(*pos, **kwargs):
        nonlocal model
        model = original_policy(*pos, **kwargs)
        return model

    def make_processors(*pos, **kwargs):
        nonlocal processors
        if cfg.policy.type == "xvla" and not cfg.resume:
            # Build the single-arm auto-mode pipeline. The generic base processor
            # can carry end-effector transforms from its pretraining setup.
            from lerobot.policies.xvla.processor_xvla import make_xvla_pre_post_processors

            processors = make_xvla_pre_post_processors(cfg.policy, kwargs["dataset_stats"])
        else:
            processors = original_processors(*pos, **kwargs)
        return processors

    def check_holdout(step, active_accelerator):
        if not step or step % 1000 or validation_recorded(validation_csv, step):
            return
        if stopped or time.monotonic() >= args.soft_deadline:
            raise DeadlineReached
        pre, post = processors
        result = validate(cfg, model, pre, post, active_accelerator, args.dataset, split, args.soft_deadline)
        append_csv(validation_csv, VAL_FIELDS, [step, *result])

    def restore(*pos, **kwargs):
        restored = original_restore(*pos, **kwargs)
        if restored != initial:
            raise ValueError("Checkpoint step disagrees with queue")
        return restored

    def update(tracker, *pos, **kwargs):
        nonlocal accelerator, last_update_end, stopped
        accelerator = kwargs["accelerator"]
        if accelerator.num_processes != 1 or accelerator.device.type != "cuda":
            raise RuntimeError("Expected one CUDA training process")
        if last_update_end is None and not args.probe_steps:
            check_holdout(initial, accelerator)
        if stopped or time.monotonic() >= args.soft_deadline:
            raise DeadlineReached
        step = tracker.steps + 1
        started = time.monotonic()
        torch.cuda.reset_peak_memory_stats()
        result = original_update(tracker, *pos, **kwargs)
        finished = time.monotonic()
        seconds = finished - started + sum(tracker.metrics[k].val for k in ("dataloading_s", "preprocessing_s"))
        row = [step, *(tracker.metrics[key].val for key in ("loss", "lr", "grad_norm")),
               seconds, torch.cuda.max_memory_allocated(), torch.cuda.max_memory_reserved()]
        append_csv(train_csv, TRAIN_FIELDS, row)
        last_update_end = finished
        if args.probe_steps:
            probe_rows.append(dict(zip(TRAIN_FIELDS, row, strict=True)))
            if len(probe_rows) >= args.probe_steps:
                raise ProbeDone
        elif stopped or finished + 2 * seconds >= args.soft_deadline:
            stopped = True
            # The official loop checks this immediately after the update.
            # Restore the regular cadence before the config is serialized.
            cfg.save_freq = step
        return result

    def save(**kwargs):
        cfg.save_freq = 1000
        target = kwargs["checkpoint_dir"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # Incomplete checkpoints never appear under a numbered directory.
        with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=target.parent) as tmp:
            staging = Path(tmp)/target.name
            original_save(**{**kwargs, "checkpoint_dir": staging})
            (staging/"training_state"/"queue_complete").touch()
            staging.rename(target)

    def update_last(path):
        try:
            original_update_last(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            # Limited Windows accounts may save models but cannot create symlinks.
            # Queue resume discovers verified numbered directories independently.
            pointer = path.parent / "last.txt"
            temporary = pointer.with_suffix(".tmp")
            temporary.write_text(path.name + "\n", encoding="utf-8")
            temporary.replace(pointer)
        if stopped or time.monotonic() >= args.soft_deadline:
            raise DeadlineReached
        check_holdout(int(path.name), accelerator)

    class ProbeDone(Exception):
        pass

    trainer.TrainPipelineConfig.validate = setup
    trainer.make_policy = make_policy
    trainer.make_pre_post_processors = make_processors
    trainer.resume_before_prepare = restore
    trainer.update_policy = update
    trainer.save_checkpoint = save
    trainer.update_last_checkpoint = update_last
    signal.signal(signal.SIGTERM, request_stop)
    sys.argv = [sys.argv[0], *trainer_args]
    try:
        if initial == args.target and not validation_recorded(validation_csv, initial):
            from accelerate import Accelerator
            from lerobot.configs.policies import PreTrainedConfig
            from lerobot.configs.train import TrainPipelineConfig
            from lerobot.policies.factory import get_policy_class

            checkpoint = args.checkpoint
            cfg = TrainPipelineConfig.from_pretrained(checkpoint)
            policy_cfg = PreTrainedConfig.from_pretrained(checkpoint)
            model = get_policy_class(policy_cfg.type).from_pretrained(checkpoint, config=policy_cfg)
            processors = original_processors(policy_cfg, str(checkpoint))
            check_holdout(initial, Accelerator(mixed_precision=cfg.accelerator.mixed_precision))
        else:
            trainer.main()
    except ProbeDone:
        seconds = probe_rows[-1]["seconds"]
        gpu = torch.cuda.get_device_properties(0)
        report = dict(steps=probe_rows, batch_size=cfg.batch_size, gpu=gpu.name,
                      gpu_bytes=gpu.total_memory, model_steps=args.target,
                      estimated_train_seconds=seconds * args.target,
                      includes_validation_and_checkpointing=False)
        args.probe_result.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except DeadlineReached:
        return 75
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--initial", type=int, default=0)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--soft-deadline", type=float, required=True)
    parser.add_argument("--hard-deadline", type=float, required=True)
    parser.add_argument("--probe-steps", type=int, default=0)
    parser.add_argument("--probe-result", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--base-repo")
    parser.add_argument("--base-revision")
    worker, trainer_args = parser.parse_known_args()
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, max(0.001, worker.hard_deadline - time.monotonic()))
    sys.exit(run(worker, trainer_args))
