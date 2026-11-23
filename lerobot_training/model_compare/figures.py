"""Render 300 dpi loss curves and the measured blue-basket comparison."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter, NullFormatter
import numpy as np

AXES = ["어깨 회전", "어깨 들기", "팔꿈치", "손목 굽힘", "손목 회전", "그리퍼"]

MODELS = ("rgb_mlp", "rgb_mlp_task")
LABELS = ("영상·상태", "영상·상태 + 지시")
COLORS = ("#24678D", "#B84F30")
GRAY = "#263849"


def read(p):
    return json.loads(p.read_bytes())


def setup(font):
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus":False,"axes.spines.top":False,"axes.spines.right":False,
                         "axes.labelcolor":GRAY,"text.color":GRAY,"axes.titleweight":"bold",
                         "figure.facecolor":"white","font.size":10})


def render(data, runs, report, font):
    from metrics import load_fold

    setup(font)
    scores = [read(report/(m+".json")) for m in MODELS]
    lat = {x["model"]: x for x in read(report/"jetson_latency.json")}
    split = read(report/"split.json")
    held = load_fold(data,split,"eval")
    rep = min(i for i,(c,_) in held.items() if c=="blue")
    ep = next(r for r in scores[0]["episodes"] if r["episode"]==rep)
    truth = held[rep][1]
    estimates = [np.load(runs/m/f"episode_{rep}.npy") for m in MODELS]
    t = np.arange(len(truth))/30
    def save(f, name):
        f.savefig(report/(name+".png"),dpi=300,bbox_inches="tight",pad_inches=.22)
        plt.close(f)
    def mae(ax):
        x = np.arange(5)
        for i,s in enumerate(scores):
            ax.bar(x+(i-.5)*.34,s["mae"][:5],.34,color=COLORS[i],label=LABELS[i])
        ax.set(xticks=x, xticklabels=AXES[:5], ylabel="평균 절대 오차 (°)",title="평가 21회 · 팔 관절 오차")
        ax.legend(frameon=False,ncol=2,fontsize=8)
    def scatter(ax):
        for i,m in enumerate(MODELS):
            x = lat[m]["mean_ms"]
            y = np.mean(scores[i]["mae"][:5])
            ax.scatter(x,y,s=95,color=COLORS[i],label=LABELS[i])
        ax.set(xlabel="Jetson 평균 지연 (ms / 16개 행동)",ylabel="팔 5축 평균 MAE (°)",
               title="평가 오차와 Jetson 지연")
        ax.margins(.35)
        ax.legend(frameon=False,fontsize=8)
    def trajectory(ax,j):
        ax.plot(t,truth[:,j],color=GRAY,label="시연 정답",lw=1.5)
        for i,a in enumerate(estimates):
            ax.plot(t,a[:,j],color=COLORS[i],label=LABELS[i],lw=1,alpha=.9)
        ax.axvline(ep["close_true"]/30,ls="--",color="#747B82",lw=1,label="기록 닫힘")
        ax.set(title=AXES[j],ylabel="점" if j==5 else "각도 (°)",xlabel="회차 시간 (초)")
    f,ax=plt.subplots(1,2,figsize=(10,3.7),layout="constrained",width_ratios=[3,1])
    mae(ax[0])
    ax[1].bar([0,1],[s["mae"][5] for s in scores],color=COLORS,width=.55)
    ax[1].set(xticks=[0,1],xticklabels=["기본","지시 추가"],ylabel="평균 절대 오차 (점)",title="그리퍼")
    save(f,"01_mae")
    f,axes=plt.subplots(2,3,figsize=(11,6),layout="constrained")
    for j,ax in enumerate(axes.flat):
        bp=ax.boxplot([[r["mae"][j] for r in s["episodes"]] for s in scores],patch_artist=True,
                      tick_labels=["기본","지시 추가"],showfliers=True)
        for b,c in zip(bp["boxes"],COLORS):
            b.set_facecolor(c); b.set_alpha(.65)
        ax.set(title=AXES[j],ylabel="회차 MAE (점)" if j==5 else "회차 MAE (°)")
    f.suptitle("평가 21회 · 회차별 오차 분포")
    save(f,"02_episode_boxes")
    f,axes=plt.subplots(2,3,figsize=(12,6),layout="constrained")
    for j,ax in enumerate(axes.flat):
        trajectory(ax,j)
    axes.flat[0].legend(fontsize=8,frameon=False)
    f.suptitle(f"파랑 평가 회차 {rep} · 16프레임씩 오픈루프 예측")
    save(f,"03_trajectories")
    f,ax=plt.subplots(figsize=(6,3.7),layout="constrained")
    scatter(ax);save(f,"05_latency_mae")

MODEL_NAMES = {
    "rgb_mlp": "영상·상태 MLP", "rgb_mlp_task": "영상·상태 + 지시 MLP",
    "act": "ACT", "act_task": "ACT + 지시", "diffusion": "Diffusion",
    "smolvla": "SmolVLA", "pi05": "π0.5", "xvla": "X-VLA",
}


def series(path, field):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return [(int(row["step"]), float(row[field])) for row in csv.DictReader(stream)
                if row.get(field) and np.isfinite(float(row[field]))]


def save_figure(fig, report, name):
    fig.savefig(report/(name+".png"), dpi=300, facecolor="white", bbox_inches="tight", pad_inches=.22)
    plt.close(fig)


def render_csv_curves(runs, report):
    for model, label in MODEL_NAMES.items():
        if model in MODELS:
            train_file = val_file = report/(model+"_loss.csv")
            train_field, val_field, ylabel = "train", "val", "정규화 MSE"
        else:
            train_file = runs/model/"train_metrics.csv"
            val_file = runs/model/"validation_metrics.csv"
            train_field, val_field, ylabel = "loss", "val_loss", "정책별 손실"
        if not train_file.is_file():
            continue
        train = series(train_file, train_field)
        val = series(val_file, val_field) if val_file.is_file() else []
        if not train:
            continue
        fig, ax = plt.subplots(figsize=(7, 3.9), layout="constrained")
        for data, color, name in ((train, COLORS[0], "학습"), (val, COLORS[1], "검증")):
            if data:
                x, y = zip(*data)
                ax.plot(x, y, color=color, linewidth=1.45, label=name,
                        marker="o" if len(x) <= 100 else None, markersize=3)
        if all(value > 0 for _, value in train+val):
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set(title=label, xlabel="학습 스텝", ylabel=ylabel)
        ax.grid(axis="y", alpha=.17)
        ax.legend(frameon=False)
        save_figure(fig, report, "04_loss_"+model)


def compared_scores(evaluations, report):
    for model, label in MODEL_NAMES.items():
        if model in MODELS:
            both = report/(model+"_color_metrics.json")
            if both.is_file():
                payload = read(both)
                yield model, label, payload["blue_7"], payload["all_21"]
        else:
            blue, all_colors = (evaluations/model/f for f in ("metrics_blue.json", "metrics.json"))
            if blue.is_file() and all_colors.is_file():
                yield model, label, read(blue), read(all_colors)


def render_summary(evaluations, report):
    compared = list(compared_scores(evaluations, report))
    if not compared:
        return
    fig, panels = plt.subplots(2, 2, figsize=(12, 7.9))
    plt.subplots_adjust(left=.09, right=.97, top=.83, bottom=.20, wspace=.25, hspace=.52)
    panel_specs = (
        ("arm5_mae_deg", "팔 5축 평균 MAE", "°", None),
        ("gripper_mae_points", "그리퍼 MAE", "점", None),
        ("close_pose_arm5_mae_deg", "시연의 그리퍼 닫힘 시점 팔 자세 MAE", "°", "close_pose_n"),
        ("close_timing_mae_s", "닫힘 시각 MAE", "초", "close_matched"),
    )
    labels = [label for _, label, _, _ in compared]
    positions = np.arange(len(labels))
    width = .29
    for ax, (key, title, unit, denominator) in zip(panels.flat, panel_specs, strict=True):
        for index, (scope, color) in enumerate((("파랑 7회", COLORS[0]), ("전체 21회", "#A9BAC8"))):
            records = [row[2+index] for row in compared]
            x = positions + (index-.5)*width
            values = [record[key] for record in records]
            heights = [value if value is not None else 0 for value in values]
            ax.bar(x, heights, width=width*.9, color=color, label=scope, zorder=3)
            for xx, val, row in zip(x, values, records, strict=True):
                label = f"{val:.2f}" if val is not None else "—"
                if denominator:
                    label += f"\n({row[denominator]}/{row['denominator_all']})"
                ax.annotate(label, (xx, val if val is not None else 0), xytext=(0, 5),
                            textcoords="offset points", ha="center", va="bottom", fontsize=8.5)
        ax.set(title=title, ylabel=unit, xticks=positions, xticklabels=labels, axisbelow=True)
        if len(labels) > 3:
            ax.tick_params(axis="x", labelrotation=22)
        ax.grid(axis="y", alpha=.17)
        ax.margins(y=.38 if denominator else .25)
    fig.suptitle("MediFlow | 파란 바구니 오프라인 평가", fontsize=20, fontweight="bold", y=.965)
    handles, legend = panels[0,0].get_legend_handles_labels()
    fig.legend(handles, legend, loc="upper center", bbox_to_anchor=(.5,.923), frameon=False, ncols=2)
    basket = "    |    ".join(f"{label} {blue['correct']}/{blue['denominator_all']} · 전체 "
                            f"{whole['correct']}/{whole['denominator_all']}"
                            for _, label, blue, whole in compared)
    if len(compared) <= 3:
        fig.text(.09, .119, "기록 기준 바구니 방향     " + basket, fontsize=9)
    latency_path = report/"jetson_rgb_latency.json"
    if latency_path.is_file():
        records = read(latency_path)
        names = {model: label for model, label, _, _ in compared}
        timings = "    |    ".join(f"{names[r['model']]} {r['mean_ms']:.2f} / {r['p95_ms']:.2f} ms"
                                 for r in records if r["model"] in names)
        if timings:
            fig.text(.09, .084, "Jetson RGB 전처리 포함 평균 / p95     " + timings, fontsize=9)
    fig.text(.09, .049, "닫힘 패널의 분수는 산출 회차 / 전체 회차입니다. 방향은 학습 시연과의 궤적 유사도로 판정했습니다.",
             fontsize=8.5, color="#54677B")
    save_figure(fig, report, "06_summary")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("dataset","runs","report","font"):
        p.add_argument(key,type=Path)
    p.add_argument("--summary-only", action="store_true")
    p.add_argument("--evaluations", type=Path, help="Model-specific evaluation output root (default: runs)")
    a=p.parse_args()
    setup(a.font)
    if not a.summary_only:
        render(a.dataset,a.runs,a.report,a.font)
    render_csv_curves(a.runs,a.report)
    render_summary(a.evaluations or a.runs,a.report)
