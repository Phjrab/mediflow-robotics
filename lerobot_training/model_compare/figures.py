"""Render publication figures for the two measured CPU comparison controls."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter, NullFormatter
import numpy as np

from metrics import AXES, load_fold

MODELS = ("rgb_mlp", "rgb_mlp_task")
LABELS = ("영상·상태", "영상·상태 + 지시")
COLORS = ("#24678D", "#B84F30")
GRAY = "#263849"


def read(p):
    return json.loads(p.read_bytes())


def render(data, runs, report, font):
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus":False,"axes.spines.top":False,"axes.spines.right":False,
                         "axes.labelcolor":GRAY,"text.color":GRAY,"axes.titleweight":"bold",
                         "figure.facecolor":"white","font.size":10})
    scores = [read(report/(m+".json")) for m in MODELS]
    hist = [read(report/(m+"_history.json")) for m in MODELS]
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
    def loss(ax,i):
        rows = hist[i]["history"]
        for k,label,style in (("train","학습 배치","-"),("val","평가 21회","--")):
            ax.plot([r["step"] for r in rows],[r[k] for r in rows],style,label=label,lw=1.8)
        ax.set(title=LABELS[i]+" · 정규화 MSE",xlabel="학습 스텝",ylabel="손실 (로그 축)",yscale="log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.legend(frameon=False,fontsize=8)
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
    for i,m in enumerate(MODELS):
        f,ax=plt.subplots(figsize=(6,3.5),layout="constrained")
        loss(ax,i)
        save(f,"04_loss_"+m)
    f,ax=plt.subplots(figsize=(6,3.7),layout="constrained")
    scatter(ax);save(f,"05_latency_mae")
    f,axes=plt.subplots(2,2,figsize=(12,7),layout="constrained")
    mae(axes[0,0]);scatter(axes[0,1]);trajectory(axes[1,0],5);loss(axes[1,1],1)
    axes[1,0].set_title(f"파랑 회차 {rep} · 그리퍼")
    axes[1,0].legend(frameon=False,fontsize=7,ncol=2)
    f.suptitle("MediFlow | 소형 정책 2종의 오프라인 비교",fontsize=18)
    save(f,"06_summary")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for key in ("dataset","runs","report","font"):
        p.add_argument(key,type=Path)
    a=p.parse_args()
    render(a.dataset,a.runs,a.report,a.font)
