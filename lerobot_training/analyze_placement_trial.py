"""Inspect saved rollout diagnostics without connecting to robot hardware."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trial", type=Path)
    args = parser.parse_args()
    trial = args.trial
    trace_path = next(trial.glob("*.joints.jsonl"))
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    times = np.array([row["elapsed_s"] for row in rows])
    names = list(rows[0]["measured"])
    values = {
        kind: np.array([[row[kind][name] for name in names] for row in rows])
        for kind in ("measured", "policy_target_pre_clamp", "command_post_clamp")
    }
    active = times < 20
    summary = {"samples": len(rows), "last_sample_s": float(times[-1]),
               "initial_state": rows[0]["measured"], "joints": {}}
    for col, name in enumerate(names):
        measured = values["measured"][:, col]
        target = values["policy_target_pre_clamp"][:, col]
        sent = values["command_post_clamp"][:, col]
        changed = np.abs(sent - target) > 0.01
        summary["joints"][name] = {
            "measured_min": float(measured.min()), "measured_max": float(measured.max()),
            "final_measured": float(measured[-1]),
            "clipped_first20_count": int(changed[active].sum()),
            "first20_count": int(active.sum()),
            "preclamp_gap_first20_p95": float(np.percentile(np.abs(target-measured)[active],95)),
            "next_observation_vs_sent_first20_p95": float(np.percentile(np.abs(measured[1:]-sent[:-1])[active[:-1]],95)),
        }
    summary["timing"] = {k: {"median_s": float(np.median([r[k] for r in rows])),
                              "p95_s": float(np.percentile([r[k] for r in rows],95)),
                              "max_s": float(max(r[k] for r in rows))}
                         for k in ("observation_s", "process_s", "infer_and_send_s", "loop_work_s")}
    # This identifies a command transition, not a visually verified release.
    grip = values["policy_target_pre_clamp"][:, names.index("gripper.pos")]
    opened = np.flatnonzero(grip > 35)
    if opened.size:
        closed = np.flatnonzero((times > times[opened[0]]) & (grip < 30))
        if closed.size:
            reopened = np.flatnonzero((times > times[closed[0]] + .2) & (grip > 35))
            if reopened.size:
                summary["gripper_reopen_command_heuristic"] = rows[int(reopened[0])]
    (trial / "analysis.json").write_text(json.dumps(summary, indent=2)+"\n")
    fig, axes = plt.subplots(3, 2, figsize=(13, 10), sharex=True)
    for col, (name, ax) in enumerate(zip(names, axes.flat)):
        for kind, label, color in (("policy_target_pre_clamp", "Model target", "#d36b27"),
                                   ("command_post_clamp", "Sent after clamp", "#2b78b8"),
                                   ("measured", "Measured", "#262626")):
            ax.plot(times, values[kind][:, col], label=label, color=color, linewidth=1)
        ax.set_title(name)
        ax.set_xlim(0, min(25, max(3, times[-1])))
        ax.grid(alpha=.2)
    axes[0,0].legend(fontsize=8)
    for ax in axes[-1]: ax.set_xlabel("Seconds since rollout start")
    fig.tight_layout()
    fig.savefig(trial / "joint_commands_first25s.png", dpi=150)
    plt.close(fig)

    frame_dir = trace_path.with_suffix(".frames")
    manifest = [json.loads(line) for line in (frame_dir / "manifest.jsonl").read_text().splitlines()]
    for camera in ("ceiling_vertical", "ceiling_oblique"):
        frames = [item for item in manifest if item["camera"] == camera][:12]
        fig, axes = plt.subplots(4, 3, figsize=(15, 15))
        for ax in axes.flat:
            ax.axis("off")
        for ax, item in zip(axes.flat, frames):
            with Image.open(frame_dir / item["file"]) as image:
                ax.imshow(np.asarray(image))
            ax.set_title(f"{item['elapsed_s']:.2f} s")
            ax.axis("off")
        fig.suptitle(camera)
        fig.tight_layout()
        fig.savefig(trial / f"{camera}_first24s.png", dpi=120)
        plt.close(fig)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
