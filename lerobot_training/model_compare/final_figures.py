"""Render the final-checkpoint comparison from measured episode and Jetson results."""
import argparse
from pathlib import Path

from figures import AXES, GRAY, read, render_csv_curves, save_figure, series, setup
from metrics import load_fold
import matplotlib.pyplot as plt
import numpy as np

NAMES = {"smolvla": "SmolVLA", "act_task": "ACT + 지시", "act": "ACT",
         "diffusion": "Diffusion", "xvla": "X-VLA"}
COLORS = dict(zip(NAMES, ("#126782", "#B75326", "#8061A6", "#558047", "#C38A21"), strict=True))


def blue_bars(ax, records, key, title, unit):
    for y, (model, record) in enumerate(records.items()):
        value = record[key]
        if value is None:
            ax.text(0, y, "매칭 없음", va="center", fontsize=8)
            continue
        ax.barh(y, value, height=.59, color=COLORS[model])
        label = f" {value:.2f}"
        if key == "close_timing_mae_s":
            label += f"  ({record['close_matched']}/7)"
        ax.text(value, y, label, va="center", fontsize=9)
    ax.set(yticks=range(len(records)), yticklabels=[NAMES[m] for m in records],
           xlabel=unit, title=title)
    ax.set_xlim(0, max(ax.get_xlim()[1]*1.35, .1))
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=.15)
    ax.set_axisbelow(True)


def render(report, dataset, predictions, font):
    setup(font)
    render_csv_curves(report, report)
    blue = {m: read(report/m/"metrics_blue.json") for m in NAMES}
    full = {m: read(report/m/"metrics.json") for m in NAMES}
    split = read(report/"split.json")
    held = load_fold(dataset, split, "eval")
    episode = min(i for i, (color, _) in held.items() if color == "blue")
    truth = held[episode][1]
    trajectories = {m: np.load(predictions/m/f"episode_{episode}.npy", allow_pickle=False) for m in NAMES}
    if any(a.shape != truth.shape for a in trajectories.values()):
        raise ValueError("Episode trajectories and recorded actions must cover the same six axes and frames")
    times = np.arange(len(truth)) / 30
    reference = next(row for row in blue["smolvla"]["episodes"] if row["episode"] == episode)
    def trajectory(ax, joint):
        for m, a in trajectories.items():
            ax.plot(times, a[:, joint], lw=1.0, alpha=.9, label=NAMES[m], color=COLORS[m])
        ax.plot(times, truth[:, joint], color="#262D34", lw=1.6, label="시연 행동", zorder=6)
        if reference["close_true"] is not None:
            ax.axvline(reference["close_true"]/30, ls=":", color=GRAY, label="시연 닫힘")
        ax.set(xlabel="회차 시간 (초)", ylabel="점" if joint == 5 else "도 (°)", title=AXES[joint])
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout="constrained")
    for i, ax in enumerate(axes.flat):
        trajectory(ax, i)
    axes.flat[0].legend(ncols=2, fontsize=7)
    fig.suptitle(f"파랑 회차 {episode} · 마지막 체크포인트의 행동 궤적", fontweight="bold")
    save_figure(fig, report, "03_trajectories")

    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout="constrained")
    for i, ax in enumerate(axes.flat):
        boxes = ax.boxplot([[row["mae"][i] for row in s["episodes"]] for s in full.values()],
                           patch_artist=True, tick_labels=list(NAMES.values()), widths=.55)
        for box, col in zip(boxes["boxes"], COLORS.values(), strict=True):
            box.set_facecolor(col)
            box.set_alpha(.76)
        ax.tick_params(axis="x", labelrotation=18, labelsize=8)
        ax.set(title=AXES[i], ylabel="회차 MAE (점)" if i == 5 else "회차 MAE (°)")
    fig.suptitle("전체 21회 · 회차별 행동 오차 분포", fontweight="bold")
    save_figure(fig, report, "02_episode_boxes")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), width_ratios=[2.6,1], layout="constrained")
    x = np.arange(5)
    for j, (m, data) in enumerate(full.items()):
        axes[0].bar(x+(j-2)*.16, data["mae"][:5], width=.15, color=COLORS[m], label=NAMES[m])
        axes[1].bar(j, data["gripper_mae_points"], width=.65, color=COLORS[m])
    axes[0].set(xticks=x, xticklabels=AXES[:5], title="팔 관절", ylabel="프레임 가중 MAE (°)")
    axes[0].legend(ncols=3, fontsize=8)
    axes[1].set(xticks=x, xticklabels=list(NAMES.values()), title="그리퍼", ylabel="프레임 가중 MAE (점)")
    axes[1].tick_params(axis="x", labelrotation=30, labelsize=8)
    fig.suptitle("전체 21회 · 관절별 행동 오차", fontweight="bold")
    save_figure(fig, report, "01_mae")

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), layout="constrained")
    blue_bars(axes[0], blue, "close_timing_mae_s", "닫힘 시각 · 매칭 회차 수", "평균 절대 오차 (초)")
    blue_bars(axes[1], blue, "close_pose_arm5_mae_deg", "시연 닫힘 순간 팔 5축", "평균 절대 오차 (°)")
    blue_bars(axes[2], blue, "gripper_mae_points", "전 구간 그리퍼", "평균 절대 오차 (점)")
    fig.suptitle("약통 A → 파란 바구니 · 평가 7회", fontweight="bold")
    save_figure(fig, report, "07_blue_grasp")

    # Rows are one-to-one with measured runtime configurations in the report.
    # Failed loads retain their error in the table and have no latency coordinate.
    rows = read(report/"jetson_comparison.json")
    measured = [r for r in rows if r.get("mean_ms") is not None and r.get("p95_ms") is not None]
    fig, ax = plt.subplots(figsize=(10, max(4, .48*len(measured)+2.2)), layout="constrained")
    if measured:
        y = np.arange(len(measured))
        for label, field, shift, color in (("평균", "mean_ms", -.14, "#126782"), ("p95", "p95_ms", .14, "#B75326")):
            ax.scatter([r[field] for r in measured], y+shift, s=33, c=color, label=label)
        ax.set_yticks(y, [f"{r['label']}  ({r['chunk_actions']}개)" for r in measured])
        ax.set_xscale("log")
        ax.invert_yaxis()
        ax.legend()
        ax.grid(axis="x", which="both", alpha=.17)
    else:
        ax.text(.5,.5,"측정 가능한 구성 없음", transform=ax.transAxes, ha="center")
    unavailable = [r["label"] for r in rows if r not in measured]
    if unavailable:
        fig.text(.01, -.105, "지연 미측정 · " + ", ".join(unavailable), fontsize=9, color=GRAY)
    ax.set(xlabel="저장 RGB 전처리부터 CPU 행동 반환까지 (ms)", title="Jetson · 구성별 묶음 지연")
    save_figure(fig, report, "05_latency_mae")

    fig, panels = plt.subplots(2, 2, figsize=(13, 8.4), layout="constrained")
    blue_bars(panels[0,0], blue, "close_pose_arm5_mae_deg", "파랑 7회 · 닫힘 자세", "팔 5축 MAE (°)")
    ax = panels[0,1]
    for r in measured:
        m = r["model"]
        if m in blue and r["mode"] == "chunk":
            ax.scatter(r["mean_ms"], blue[m]["close_pose_arm5_mae_deg"], s=66, color=COLORS[m], label=NAMES[m])
    if ax.collections:
        ax.set_xscale("log")
        ax.legend(ncols=2, fontsize=8)
    else:
        ax.text(.5,.5,"공통 측정값 없음", transform=ax.transAxes, ha="center")
    ax.set(title="묶음 지연과 파랑 닫힘 자세", xlabel="Jetson 평균 지연 (ms)", ylabel="팔 5축 MAE (°)")
    trajectory(panels[1,0], 5)
    panels[1,0].set_title(f"파랑 회차 {episode} · 그리퍼")
    panels[1,0].legend(fontsize=7, ncols=3)
    ax = panels[1,1]
    train = series(report/"smolvla/train_metrics.csv", "loss")
    val = series(report/"smolvla/validation_metrics.csv", "val_loss")
    x, losses = zip(*train)
    ax.plot(x[199:], np.convolve(losses, np.ones(200)/200, "valid"), color=COLORS["smolvla"], label="학습 200스텝 평균")
    ax.plot(*zip(*val), "-o", ms=4, color="#B75326", label="검증 21회")
    ax.set(xlabel="스텝", ylabel="SmolVLA loss", title="SmolVLA 학습·검증")
    ax.legend(fontsize=8)
    fig.suptitle("약통 A → 파란 바구니 · 최종 체크포인트 비교", fontsize=18, fontweight="bold")
    save_figure(fig, report, "06_summary")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("report", "dataset", "predictions", "font"):
        parser.add_argument(key, type=Path)
    args = parser.parse_args()
    render(args.report, args.dataset, args.predictions, args.font)
