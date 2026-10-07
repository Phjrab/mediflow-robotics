#!/usr/bin/env python3
"""Export comparable ACT A/B/C training curves from the systemd journals."""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path("/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2")
OUTPUT_DIR = PROJECT_ROOT / "outputs/training_reports/act_abc_20260929"
METRICS = ("loss", "l1_loss", "kld_loss", "grdn", "mem_gb", "smp/s")


def journal(unit: str) -> str:
    return subprocess.run(
        ["journalctl", "--user", "-u", unit, "-o", "cat", "--no-pager"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.replace("\r", "\n")


def parse_metric_line(line: str) -> dict[str, float]:
    row: dict[str, float] = {}
    for key in METRICS:
        match = re.search(rf"(?:^|\s){re.escape(key)}:([^\s]+)", line)
        if match:
            row[key] = float(match.group(1))
    return row


def load_curves() -> dict[str, list[dict[str, float]]]:
    curves: dict[str, list[dict[str, float]]] = {"A": [], "B": [], "C": []}

    c_raw = journal("mediflow-act-c-10000-20260928-v2.service")
    for line in c_raw.splitlines():
        if "ot_train.py:641" in line and " loss:" in line:
            curves["C"].append(parse_metric_line(line))

    phase: str | None = None
    ab_raw = journal("mediflow-act-b-then-a-10000-20260928.service")
    for line in ab_raw.splitlines():
        if "[B] starting:" in line:
            phase = "B"
        elif "[A] starting:" in line:
            phase = "A"
        elif phase and "ot_train.py:641" in line and " loss:" in line:
            curves[phase].append(parse_metric_line(line))

    for label, rows in curves.items():
        if len(rows) != 200:
            raise RuntimeError(f"{label}: expected 200 records, found {len(rows)}")
        for index, row in enumerate(rows, start=1):
            row["step"] = index * 50
    return curves


def write_csv(curves: dict[str, list[dict[str, float]]]) -> Path:
    path = OUTPUT_DIR / "act_abc_training_metrics.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("model", "step", *METRICS))
        writer.writeheader()
        for label in ("C", "B", "A"):
            for row in curves[label]:
                writer.writerow({"model": label, **row})
    return path


def write_plot(curves: dict[str, list[dict[str, float]]]) -> Path:
    colors = {"A": "#ef4444", "B": "#2563eb", "C": "#16a34a"}
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    panels = (
        ("loss", "Total loss", None),
        ("l1_loss", "L1 action loss", None),
        ("loss", "Total loss (steps 5k–10k)", (5000, 10000)),
    )
    for axis, (metric, title, limits) in zip(axes, panels, strict=True):
        for label in ("C", "B", "A"):
            rows = curves[label]
            axis.plot(
                [row["step"] for row in rows],
                [row[metric] for row in rows],
                color=colors[label],
                linewidth=1.6,
                label=f"ACT-{label}",
            )
        if limits:
            axis.set_xlim(*limits)
            visible = [
                row[metric]
                for label in ("C", "B", "A")
                for row in curves[label]
                if limits[0] <= row["step"] <= limits[1]
            ]
            low, high = min(visible), max(visible)
            padding = max((high - low) * 0.1, 0.005)
            axis.set_ylim(low - padding, high + padding)
        axis.set_title(title)
        axis.set_xlabel("Training step")
        axis.set_ylabel(metric)
        axis.grid(alpha=0.25)
        axis.legend()
    fig.suptitle("SO-ARM101 ACT training — A/B/C separate 3-camera policies", fontsize=14)
    path = OUTPUT_DIR / "act_abc_training_curves.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    curves = load_curves()
    csv_path = write_csv(curves)
    plot_path = write_plot(curves)
    for label in ("C", "B", "A"):
        rows = curves[label]
        print(
            f"ACT-{label}: first={rows[0]['loss']:.3f} "
            f"final={rows[-1]['loss']:.3f} best={min(row['loss'] for row in rows):.3f}"
        )
    print(f"CSV={csv_path}")
    print(f"PLOT={plot_path}")


if __name__ == "__main__":
    main()
