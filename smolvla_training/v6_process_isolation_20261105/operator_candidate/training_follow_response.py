"""Read the exact three-color training rows and compare leader goals with follower state."""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from training_motion_stats import EXPECTED, JOINTS, TRIMMED_LENGTHS


def crossing_frames(actions, states, stability=.35, amplitude=2., horizon=15):
    """Find separated commanded transitions with a stable three-frame baseline and four-frame plateau."""
    from statistics import median
    reached, censored, pre_reached = [], 0, 0
    t = 3
    while t + 6 < len(actions):
        pre = actions[t-3:t]
        baseline = median(pre)
        if max(pre)-min(pre) > stability or abs(actions[t]-baseline) <= stability:
            t += 1
            continue
        end = None
        for k in range(t,min(t+7,len(actions)-3)):
            post = actions[k:k+4]
            if max(post)-min(post) <= stability and abs(median(post)-baseline) >= amplitude:
                end = k
                break
        if end is None:
            t += 1
            continue
        target = median(actions[end:end+4])
        state_base = median(states[t-3:t])
        direction = 1 if target>baseline else -1
        movement = direction*(target-state_base)
        threshold = .63*movement
        if movement < amplitude or direction*(states[t-1]-state_base) >= threshold:
            pre_reached += 1
            t = end+4
            continue
        window_end = min(len(states),t+horizon+1)
        # Once the requested motion has clearly reversed, no later crossing is credited to this event.
        for k in range(end+4,window_end):
            if direction*(actions[k]-baseline) < .63*abs(target-baseline):
                window_end = k
                break
        first = next((k for k in range(t,window_end) if direction*(states[k]-state_base)>=threshold),None)
        if first is None:
            censored += 1
        else:
            reached.append(first-t)
        t = max(t+1,window_end)
    return reached,censored,pre_reached


def calculate(base, mapping, previous):
    import numpy as np
    import pyarrow.parquet as pq

    if hashlib.sha256(mapping.read_bytes()).hexdigest()!=previous["mapping_sha256"]:
        raise ValueError("학습 매핑 해시 불일치")
    with mapping.open(encoding="utf-8",newline="") as stream:
        mapped = list(csv.DictReader(stream))
    if [int(row["merged_episode_index"]) for row in mapped] != list(range(137)):
        raise ValueError("학습 회차 매핑 불일치")
    sources=defaultdict(dict)
    for row in mapped:
        sources[row["source_repo_id"].split("/")[-1]][int(row["source_episode_index"])]=row
    all_errors, all_action, all_state, minima = [], [], [], []
    counts, frames = Counter(), Counter()
    observed_hashes = {}
    lag = {j:dict(samples=[],censored=0,excluded_baseline=0) for j in JOINTS}
    for source,selection in sources.items():
        root=base/source
        info=json.loads((root/"meta/info.json").read_bytes())
        names=[j+".pos" for j in JOINTS]
        if info["fps"]!=30 or any(info["features"][key]["names"]!=names or info["features"][key]["shape"]!=[6]
                                  for key in ("action","observation.state")):
            raise ValueError(f"{source} FPS, 순서 또는 차원 불일치")
        found=defaultdict(list)
        for path in sorted((root/"data").rglob("*.parquet")):
            key=f"{source}/{path.relative_to(root).as_posix()}"
            observed_hashes[key]=hashlib.sha256(path.read_bytes()).hexdigest()
            if observed_hashes[key]!=previous["parquet_sha256"].get(key):
                raise ValueError(f"학습 원본 해시 불일치: {key}")
            for batch in pq.ParquetFile(path).iter_batches(columns=["episode_index","frame_index","timestamp","action","observation.state"]):
                for row in batch.to_pylist():
                    if row["episode_index"] in selection:
                        found[row["episode_index"]].append(row)
        if set(found)!=set(selection):
            raise ValueError(f"{source} 회차 누락")
        for number,row in selection.items():
            items=sorted(found[number],key=lambda r:r["frame_index"])
            if [r["frame_index"] for r in items]!=list(range(len(items))):
                raise ValueError("프레임 순서 불일치")
            length=TRIMMED_LENGTHS.get((source,number))
            if length is not None:
                if len(items) not in (length,length+1):
                    raise ValueError("잘라낸 회차 길이 불일치")
                items=items[:length]
            a,s=(np.asarray([r[k] for r in items],dtype=float) for k in ("action","observation.state"))
            if a.shape!=s.shape or a.shape!=(len(items),6) or not np.isfinite(a).all() or not np.isfinite(s).all() or not np.allclose(
                    np.diff([r["timestamp"] for r in items]),1/30,atol=.001,rtol=0):
                raise ValueError("학습 시계열 불일치")
            all_action.append(a)
            all_state.append(s)
            all_errors.append(a-s)
            color=row["color"]
            counts[color]+=1
            frames[color]+=len(items)
            minima.append(dict(episode=int(row["merged_episode_index"]),source=source,
                               source_episode_index=number,color=color,
                               action_min=float(a[:,2].min()),state_min=float(s[:,2].min()),
                               state_at_start=float(s[0,2]),action_at_start=float(a[0,2])))
            for i,j in enumerate(JOINTS):
                samples,censored,excluded=crossing_frames(a[:,i],s[:,i],stability=.5 if i==5 else .35)
                lag[j]["samples"].extend(samples)
                lag[j]["censored"]+=censored
                lag[j]["excluded_baseline"]+=excluded
    if observed_hashes!=previous["parquet_sha256"] or {c:(counts[c],frames[c]) for c in counts}!=EXPECTED:
        raise ValueError("전체 학습 근거 불일치")
    actions,states,errors=(np.concatenate(rows) for rows in (all_action,all_state,all_errors))
    def summary(values, quantiles, keys):
        return dict(zip(keys,map(float,np.percentile(values,quantiles)))) if len(values) else None
    joints={}
    for i,j in enumerate(JOINTS):
        metric=lag[j]
        samples=metric.pop("samples")
        joints[j]=dict(unit="degree" if i<5 else "gripper_point_0_100",
                       signed_action_minus_state=summary(errors[:,i],[50,95,100],["p50","p95","max"]),
                       absolute_error=summary(np.abs(errors[:,i]),[50,95,100],["p50","p95","max"]),
                       lag_63_frames=dict(reached=len(samples),**metric,quantiles=summary(samples,[50,95,100],["p50","p95","max"])))
    elbow=dict(
        action=summary(actions[:,2],[0,5,50,95,100],["min","p5","p50","p95","max"]),
        state=summary(states[:,2],[0,5,50,95,100],["min","p5","p50","p95","max"]),
        episodes_below={str(limit):dict(action=sum(r["action_min"]<limit for r in minima),
                                       state=sum(r["state_min"]<limit for r in minima))
                        for limit in (90,83,60,0,-60)},
        episode_minima=sorted(minima,key=lambda row:row["episode"]))
    return dict(episodes=len(minima),frames=len(states),fps=30,joints=joints,elbow=elbow,
                mapping_sha256=previous["mapping_sha256"],parquet_sha256=observed_hashes,
                lag_method=dict(baseline_frames=3,plateau_frames=4,minimum_change=2,
                                angular_plateau_span=.35,gripper_plateau_span=.5,
                                maximum_onset_to_plateau_frames=6,maximum_followup_frames=15,
                                unit="frame",censored_crossings_excluded_from_quantiles=True))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("datasets","mapping","evidence","out"):
        p.add_argument("--"+name,required=True,type=Path)
    args=p.parse_args()
    result=calculate(args.datasets,args.mapping,json.loads(args.evidence.read_bytes()))
    with args.out.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({**result,"elbow":{k:v for k,v in result["elbow"].items() if k!="episode_minima"}},ensure_ascii=True))


if __name__=="__main__":
    main()
