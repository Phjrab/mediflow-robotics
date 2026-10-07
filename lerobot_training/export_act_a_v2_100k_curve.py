#!/usr/bin/env python3
"""Export the completed ACT-A v2 100K training history as CSV and PNG."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = PROJECT_ROOT / "work/lerobot_outputs/act_a_v2_3cam_100k_20261002"
LOG_PATH = RUN_ROOT / "wandb/run-20261002_182447-5c90n0rr/files/output.log"
OUTPUT_DIR = PROJECT_ROOT / "outputs/training_reports/act_a_v2_100k_20261003"
METRICS = ("loss", "l1_loss", "kld_loss", "grdn", "mem_gb", "smp/s")


def parse() -> list[dict[str, float]]:
    text = LOG_PATH.read_text(encoding="utf-8").replace("\r", "\n")
    rows: list[dict[str, float]] = []
    for line in text.splitlines():
        if "ot_train.py:641" not in line or " loss:" not in line:
            continue
        progress = re.search(r"\|\s*(\d+)/100000", line)
        if not progress:
            continue
        row: dict[str, float] = {"step": float(progress.group(1))}
        for metric in METRICS:
            match = re.search(rf"(?:^|\s){re.escape(metric)}:([^\s]+)", line)
            if match:
                row[metric] = float(match.group(1))
        if all(metric in row for metric in METRICS):
            rows.append(row)
    if len(rows) != 2000 or rows[-1]["step"] != 100000:
        raise RuntimeError(f"expected 2000 records ending at 100000, got {len(rows)}")
    return rows


def write_csv(rows: list[dict[str, float]]) -> Path:
    path = OUTPUT_DIR / "act_a_v2_100k_training_metrics.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("step", *METRICS))
        writer.writeheader()
        writer.writerows(rows)
    return path


def moving_average(values: list[float], window: int = 25) -> list[float]:
    averaged: list[float] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        averaged.append(total / min(index + 1, window))
    return averaged


def write_plot(rows: list[dict[str, float]]) -> Path:
    steps = [row["step"] for row in rows]
    losses = [row["loss"] for row in rows]
    l1_losses = [row["l1_loss"] for row in rows]
    kld_losses = [row["kld_loss"] for row in rows]
    smooth_loss = moving_average(losses)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.7), constrained_layout=True)

    axes[0].plot(steps, losses, color="#93c5fd", linewidth=0.7, alpha=0.65, label="raw")
    axes[0].plot(steps, smooth_loss, color="#1d4ed8", linewidth=2, label="25-point mean")
    axes[0].set_yscale("log")
    axes[0].set_title("Total loss — full 100K")

    axes[1].plot(steps, l1_losses, color="#16a34a", linewidth=1.2, label="L1 action loss")
    axes[1].plot(steps, kld_losses, color="#f59e0b", linewidth=1.0, label="KLD loss")
    axes[1].set_yscale("log")
    axes[1].set_title("Loss components")

    tail = [row for row in rows if row["step"] >= 90000]
    axes[2].plot(
        [row["step"] for row in tail],
        [row["loss"] for row in tail],
        color="#7c3aed",
        linewidth=1.2,
        label="total loss",
    )
    axes[2].axhline(rows[-1]["loss"], color="#dc2626", linestyle="--", label="final 0.0514")
    axes[2].set_title("Final 10K steps")

    for axis in axes:
        axis.set_xlabel("Training step")
        axis.set_ylabel("Loss")
        axis.grid(alpha=0.25)
        axis.legend()

    fig.suptitle("SO-ARM101 ACT-A v2 — 60 episodes, 3 cameras, 100K steps", fontsize=14)
    path = OUTPUT_DIR / "act_a_v2_100k_training_curves.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = parse()
    csv_path = write_csv(rows)
    plot_path = write_plot(rows)
    print(f"records={len(rows)} first_loss={rows[0]['loss']:.3f} final_loss={rows[-1]['loss']:.3f}")
    print(f"csv={csv_path}")
    print(f"plot={plot_path}")


if __name__ == "__main__":
    main()
