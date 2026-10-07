#!/usr/bin/env python3
"""Recreate the completed ACT-C run's training curves as a W&B offline run."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import wandb


PROJECT_ROOT = Path("/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2")
OUTPUT_DIR = PROJECT_ROOT / "work/lerobot_outputs/act_c_3cam_10000_20260928_163839"
UNIT = "mediflow-act-c-10000-20260928-v2.service"

METRIC_MAP = {
    "loss": "train/loss",
    "grdn": "train/grad_norm",
    "lr": "train/lr",
    "updt_s": "train/update_s",
    "data_s": "train/dataloading_s",
    "smp/s": "train/samples_per_s",
    "mem_gb": "train/memory_gb",
    "l1_loss": "train/l1_loss",
    "kld_loss": "train/kld_loss",
}


def main() -> None:
    raw = subprocess.run(
        ["journalctl", "--user", "-u", UNIT, "-o", "cat", "--no-pager"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.replace("\r", "\n")
    lines = [line for line in raw.splitlines() if "ot_train.py:641" in line and " loss:" in line]
    if len(lines) != 200:
        raise RuntimeError(f"expected 200 metric records, found {len(lines)}")

    run = wandb.init(
        project="mediflow-so101-act",
        name="ACT-C__medicine-c-to-basket-c__3cam__10k",
        group="ABC-3cam-20260928",
        job_type="train_backfill",
        mode="offline",
        dir=str(OUTPUT_DIR),
        tags=["ACT", "medicine-C", "3-camera", "10000-steps", "backfilled"],
        notes="Recovered from the completed ACT-C systemd journal; checkpoints remain local.",
        config={
            "dataset": "Supermassive111/medicine_c_to_basket_c_3cam_test_20260928_154245",
            "episodes": 60,
            "cameras": ["ceiling_vertical", "ceiling_oblique", "end_effector"],
            "steps": 10000,
            "batch_size": 8,
            "policy": "act",
            "video_backend": "pyav",
            "use_amp": False,
        },
    )
    assert run is not None

    for index, line in enumerate(lines, start=1):
        record: dict[str, float] = {}
        for source, target in METRIC_MAP.items():
            match = re.search(rf"(?:^|\s){re.escape(source)}:([^\s]+)", line)
            if match:
                record[target] = float(match.group(1))
        wandb.log(record, step=index * 50)

    run.summary["training_status"] = "completed"
    run.summary["final_checkpoint"] = str(OUTPUT_DIR / "checkpoints/010000/pretrained_model")
    run.summary["metric_records"] = len(lines)
    run.finish()
    print(f"ACT-C_WANDB_BACKFILL_OK records={len(lines)} output={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
