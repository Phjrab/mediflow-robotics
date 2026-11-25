"""Render public physical-evidence figures from the preserved run index, traces and camera stills."""
import argparse
import csv
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from figures import AXES, read, setup

# Video verdicts and timestamps are documented in reports/model_compare/EVIDENCE_AUDIT.md.
RUNS = {
    "144002": ("14:40", True, 64.999, (57.1, 59.3)),
    "153047": ("15:30", False, 85.917, (79.1, 80.4)),
    "155459": ("15:54", True, 51.012, (44.8, 45.7)),
    "210316": ("21:03", False, None, (93.8, 94.92)),
}
STAGES = ["시작", "홈 통과", "정책 실행", "집기 자세", "닫힘", "파지 채택", "투입 확인"]
BLUE, TEAL, AMBER, GRAY = "#356a90", "#19756d", "#a65d29", "#647589"


def save(fig, path):
    fig.savefig(path, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=.28)
    plt.close(fig)


def bars(ax, labels, values, unit, color, fmt="%.0f"):
    series = ax.bar(labels, values, color=color, width=.65)
    ax.bar_label(series, fmt=fmt, padding=3, fontsize=9)
    ax.margins(y=.18)
    ax.set_ylabel(unit)
    ax.grid(axis="y", alpha=.15)
    ax.set_axisbelow(True)


def render(workspace, report, font):
    setup(font)
    index = read(workspace/".local/model_figures_work/runs.json")
    accepted = {"20261120_"+k for k, (_, keep, _, _) in RUNS.items() if keep}
    if len(index) != len({r["id"] for r in index}) or not accepted <= {r["id"] for r in index}:
        raise ValueError("Incorrect archived run index")
    rows = [[r["status"] is not None or r["points"] > 0, r["home"], r["steps"] > 0,
             r["pose"], r["closed"], r["id"] in accepted, r["id"] in accepted] for r in index]
    cumulative = [sum(all(row[:i+1]) for row in rows) for i in range(len(STAGES))]
    n = len(rows)
    if cumulative != [46,32,32,16,15,2,2] or n != 47:
        raise ValueError("Archived funnel differs from the documented cohort")
    colors = [GRAY,GRAY,GRAY,BLUE,BLUE,TEAL,TEAL]
    fig, ax = plt.subplots(figsize=(10,5.8))
    b = ax.barh(STAGES[::-1], cumulative[::-1], color=colors[::-1])
    ax.bar_label(b, labels=[f"{x}/{n}" for x in cumulative[::-1]], padding=6)
    ax.set(xlim=(0,55), xlabel="앞 단계까지 확인한 실행 수 (회)")
    ax.set_title("배포 SmolVLA 실물 투입 확인 2회 · 실물 기록 47회", loc="left", fontsize=18, pad=15)
    fig.text(.12,-.035,"파란 바구니 투입 장면 3건 중 2건 채택. 15:30은 사람이 약통을 재배치해 제외했다.\n47회에는 짧은 시험과 미판정 실행이 포함된다. 비율은 보존 자료의 확인 범위를 나타낸다.",fontsize=10)
    save(fig, report/"10_live_funnel.png")

    coord = workspace/"MediFlow_coord_20261119/tasks"
    demos = read(coord/"MF-01/COMMON_TRAIN_ONLY_MANIFEST.json")["train"]
    counts = Counter(r["color"] for r in demos)
    folder = workspace/"so101_vla/reports/20261115/GRASP_CAUSE_7_EPISODES/full_frame_evaluation"
    errors = []
    for ep in read(folder/"quality.json")["episodes"]:
        with np.load(folder/f"episode_{ep['merged_episode_index']}_actions.npz") as z:
            errors.append(np.abs(z["predictions"][:,0] - z["recorded"]))
    mae = np.concatenate(errors).mean(axis=0, dtype=np.float64)
    with (coord/"MF-03/DENOISING_STAGES.csv").open() as f:
        profile = next(csv.DictReader(f))
    ms = [float(profile[k]) for k in ("vision_inside_embed_prefix_sum_ms","vlm_prefix_kv_sum_ms","denoise_sum_ms")]
    fig, a = plt.subplots(2,2,figsize=(13.5,8),layout="constrained")
    bars(a[0,0], ["빨강","초록","파랑"], [counts[k] for k in ("red","green","blue")],"시연 (회)",["#a7514c","#428466",BLUE])
    a[0,0].set_title("학습 시연 137회 · 바구니 색별")
    bars(a[0,1],AXES[:5],mae[:5],"선두 행동 MAE (°)",TEAL,"%.2f")
    a[0,1].set_title("학습 시연 7회 · 3,050프레임 재현 오차")
    bars(a[1,0],["비전","KV 캐시 생성","디노이징 × 10"],ms,"처리 시간 (ms)",BLUE)
    a[1,0].set_title("저장 관측 한 건 · 추론 구간별 시간")
    bars(a[1,1],STAGES,cumulative,"확인한 실행 (회)",colors)
    a[1,1].set_title("실물 기록 47회 · 파지·투입 확인 2회")
    fig.suptitle("배포 SmolVLA 실물 투입 확인 2회 · 약통 A 분류 실험\n파란 바구니 투입 장면 3건 중 14:40·15:54 채택, 15:30 사람 재배치 제외",fontsize=16,fontweight="bold")
    save(fig, report/"12_slide.png")

    archive = workspace/".local/success_plots"
    traces, stills = {}, {}
    for k, (label, accepted, target, (lo,hi)) in RUNS.items():
        with np.load(archive/k/"trace.npz") as z:
            trace = z["a"]
        steps = trace[(trace[:,0] >= lo) & (trace[:,0] <= hi)]
        opening = steps[:,13]
        peak = np.maximum.accumulate(opening)
        i = next(i for i in range(len(steps)-2) if np.all(opening[i:i+3] <= peak[i]-5))
        traces[k] = steps[i,2:8] - steps[i,8:14]
        if target is not None:
            t, filename, _ = min(read(archive/k/"meta.json")["frames"],key=lambda x: abs(x[0]-target))
            stills[k] = (t,archive/k/filename)
    names = ["14:40 채택","15:30 재배치 · 제외","15:54 채택","21:03 실패"]
    contrasts = [BLUE,AMBER,TEAL,GRAY]
    fig, axes = plt.subplots(1,2,figsize=(12,5.6),width_ratios=[3,1],layout="constrained")
    for ax, idx, unit in [(axes[0],np.arange(5),"목표 - 실측 (°)"),(axes[1],np.array([5]),"목표 - 실측 (점)")]:
        for i, values in enumerate(traces.values()):
            ax.bar(np.arange(len(idx)) + (i-1.5)*.2, values[idx], .19, label=names[i], color=contrasts[i])
        ax.set_xticks(range(len(idx)),[AXES[i] for i in idx])
        ax.set_ylabel(unit)
        ax.axhline(0, color=GRAY, lw=.8)
    axes[0].legend(fontsize=9)
    fig.suptitle("실물 닫힘 순간의 정책 목표와 실측 위치 차이\n배포 SmolVLA 실물 투입 확인 2회 · 사람 재배치 1회 제외 · 대표 실패 1회",fontsize=16)
    save(fig, report/"success_runs/closure_errors.png")

    fig, axes = plt.subplots(1,3,figsize=(13,5.7),layout="constrained")
    for i,(k,(t,path)) in enumerate(stills.items()):
        ax=axes[i]
        with Image.open(path) as img:
            ax.imshow(img.crop((45,30,295,264)))
        ax.set_axis_off()
        ax.set_title(names[i]+f"\nREADY +{t:.2f}초",color=contrasts[i],fontsize=14)
    fig.suptitle("배포 SmolVLA 실물 투입 확인 2회 · 파란 바구니\n보존 영상의 투입 3건 중 15:30은 사람 재배치로 제외했다.",fontsize=16)
    fig.text(.02,-.045,"14:40은 첫 들림 전 3.30초 영상 공백이 있다. 세 장면은 사후 선정됐으며 독립 반복 시험의 성공률은 산출할 수 없다.",fontsize=10)
    save(fig, report/"success_runs/slide_success.png")


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("workspace",type=Path)
    p.add_argument("report",type=Path)
    p.add_argument("font",type=Path)
    a=p.parse_args()
    render(a.workspace,a.report,a.font)
