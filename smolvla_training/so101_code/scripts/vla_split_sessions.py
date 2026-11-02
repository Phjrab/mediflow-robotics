#!/usr/bin/env python3
"""Grouped split from explicit session evidence only; no inferred sessions."""
import argparse
from pathlib import Path
import random

from vla_data import Dataset, outside, write_new
from vla_record import read_json, RecordError
from vla_sessions import read_sessions


def session_evidence(ds,sessions=None):
    provenance=read_json(ds.root/"provenance.json") if (ds.root/"provenance.json").exists() else None
    rows=read_sessions(sessions) if sessions else []
    index={}
    for row in rows:
        key=(row["dataset_binding"]["root"],row["episode_index"])
        if key in index: raise RecordError("duplicate episode session evidence")
        index[key]=row
    source_map={e["episode_index"]:e for e in provenance["episodes"]} if provenance else {}
    if provenance and (provenance.get("derived_binding") != ds.binding or set(source_map)!={m["episode_index"] for m in ds.metas}):
        raise RecordError("derived provenance binding/index mismatch")
    result={}
    for m in ds.metas:
        i=m["episode_index"]
        lineage=source_map.get(i)
        if lineage and lineage.get("derived_fingerprint")!=ds.fingerprint(m):
            raise RecordError("derived episode fingerprint mismatch")
        root=provenance["source_binding"]["root"] if lineage else str(ds.root)
        old=lineage["source_episode_index"] if lineage else i
        row=index.get((root,old))
        if row is None and lineage and lineage.get("session_id"):
            result[i]={"session_id":lineage["session_id"],"combo":lineage.get("combo"),"source_root":root,"source_episode_index":old,
                       "source_fingerprint":lineage["source_fingerprint"]}
        elif row is not None:
            if m["tasks"]!=[row["task"]]: raise RecordError("sidecar task disagrees with dataset")
            if not lineage and row["episode_fingerprint"] != ds.fingerprint(m):
                raise RecordError("sidecar fingerprint disagrees with dataset")
            if lineage and lineage.get("source_fingerprint") != row["episode_fingerprint"]:
                raise RecordError("lineage fingerprint disagrees with sidecar")
            if lineage and lineage.get("session_id") and lineage["session_id"]!=row["session_id"]:
                raise RecordError("lineage/session disagreement")
            result[i]={"session_id":row["session_id"],"combo":row["combo"],"source_root":root,"source_episode_index":old,
                       "source_fingerprint":row["episode_fingerprint"]}
    return result


def validate_split(value):
    assigned=value["train_episodes"]+value["eval_episodes"]
    if len(set(assigned))!=len(assigned) or set(assigned)&set(value["unknown_episodes"]):
        raise RecordError("duplicate/unknown episode assignment")
    if {e["episode_index"] for e in value["lineage"]}!=set(assigned):
        raise RecordError("lineage does not cover assignment")
    if set(value["train_episodes"]) & set(value["eval_episodes"]): raise RecordError("episode leakage")
    if set(value["train_sessions"]) & set(value["eval_sessions"]): raise RecordError("session leakage")
    for e in value["lineage"]:
        assigned="train" if e["episode_index"] in value["train_episodes"] else "eval"
        if e["session_id"] not in value[assigned+"_sessions"]: raise RecordError("inconsistent session assignment")


def split(ds,evidence,ratio=.2,seed=101):
    if not 0<ratio<1: raise RecordError("eval ratio must be between 0 and 1")
    if not set(evidence)<={m["episode_index"] for m in ds.metas}:
        raise RecordError("unknown episode in session evidence")
    groups={}; combo_sessions={}
    for episode,row in evidence.items():
        groups.setdefault(row["session_id"],[]).append(episode)
        if row.get("combo"): combo_sessions.setdefault(row["combo"],set()).add(row["session_id"])
    sessions=sorted(groups); warnings=[]
    if len(sessions)<2:
        evaluation=set(); warnings.append("INSUFFICIENT_SESSIONS_FOR_EVAL")
    else:
        count=max(1,min(len(sessions)-1,round(len(sessions)*ratio)))
        rng=random.Random(seed); candidates=[]
        for _ in range(512):
            selected=set(rng.sample(sessions,count))
            missed=sum(not(s & selected and s-selected) for s in combo_sessions.values())
            candidates.append((missed,tuple(sorted(selected))))
        _,best=min(candidates); evaluation=set(best)
    train=set(sessions)-evaluation
    for combo,ss in sorted(combo_sessions.items()):
        if not(ss & evaluation and ss & train): warnings.append("COMBO_NOT_STRATIFIED:"+combo)
    known=set(evidence)
    value={"schema_version":1,"dataset_binding":ds.binding,"seed":seed,"eval_ratio":ratio,
        "train_episodes":sorted(i for s in train for i in groups[s]),"eval_episodes":sorted(i for s in evaluation for i in groups[s]),
        "train_sessions":sorted(train),"eval_sessions":sorted(evaluation),
        "unknown_episodes":[m["episode_index"] for m in ds.metas if m["episode_index"] not in known],
        "lineage":[dict(episode_index=i,**row) for i,row in sorted(evidence.items())],"warnings":warnings}
    value["unknown_count"]=len(value["unknown_episodes"])
    validate_split(value)
    return value


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--root",type=Path,required=True)
    p.add_argument("--sessions",type=Path); p.add_argument("--out",type=Path,required=True)
    p.add_argument("--eval-ratio",type=float,default=.2); p.add_argument("--seed",type=int,default=101)
    a=p.parse_args(argv); ds=Dataset(a.root); value=split(ds,session_evidence(ds,a.sessions),a.eval_ratio,a.seed)
    output=outside(ds.root,a.out); output.parent.mkdir(parents=True,exist_ok=True); write_new(output,value)
    print(f"train={len(value['train_episodes'])} eval={len(value['eval_episodes'])} unknown={value['unknown_count']}")
    return 0


if __name__=="__main__": raise SystemExit(main())
