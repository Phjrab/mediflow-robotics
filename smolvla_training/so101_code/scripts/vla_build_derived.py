#!/usr/bin/env python3
"""Default read-only exclusion plan; explicit execute writes a new dataset only."""
import argparse
from pathlib import Path
import json
import numpy as np

from vla_data import Dataset, now, outside, write_new, tree_hash, sample_hashes, official_worker
from vla_record import RecordError, read_json
from vla_review_episode import latest_reviews
from vla_split_sessions import session_evidence
from vla_audit_dataset import audit


def exclusion_plan(ds,audit_report,reviews=None):
    if audit_report["dataset_binding"]!=ds.binding: raise RecordError("audit dataset binding mismatch")
    reviewed=latest_reviews(reviews,ds.binding); excluded=[]
    for row in audit_report["episodes"]:
        reasons=[r["code"] for r in row["reasons"] if r["severity"]=="FAIL"]
        review=reviewed.get(row["episode_index"])
        if review and review["verdict"] in ("fail","hold"): reasons.append("REVIEW_"+review["verdict"].upper())
        if reasons:
            excluded.append({"episode_index":row["episode_index"],"reasons":reasons,"evidence":review or row["reasons"],"date":now()})
    if {r["episode_index"] for r in audit_report["episodes"]}!={m["episode_index"] for m in ds.metas}:
        raise RecordError("incomplete audit episode list")
    return {"schema_version":1,"dataset_binding":ds.binding,"excluded_episodes":excluded}


def build(root,report,reviews,out,execute=False,official_source=None,loader_python=None,sessions=None):
    ds=Dataset(root); plan=exclusion_plan(ds,report,reviews); output=outside(ds.root,out)
    if output.exists(): raise FileExistsError("derived output already exists")
    if not execute: return {"status":"DRY_RUN","plan":plan,"out":str(output)}
    if official_source is None: raise RecordError("official v0.6.0 source required")
    exclude=[e["episode_index"] for e in plan["excluded_episodes"]]
    keep=[m["episode_index"] for m in ds.metas if m["episode_index"] not in exclude]
    if not keep: raise RecordError("all episodes excluded")
    before=tree_hash(ds.root)
    reloaded=official_worker(ds.root,official_source,**({"python":loader_python} if loader_python else {}),
                             mode="derive",output=output,excluded=exclude)
    if reloaded["status"]!="PASS":
        if output.exists(): write_new(output/"DERIVATION_FAILURE.json",reloaded)
        raise RecordError("official derived creation failed; partial new output preserved")
    derived=Dataset(output); evidence=session_evidence(ds,sessions)
    lineage=[]; checks=[]
    for new,old in enumerate(keep):
        source_meta=next(m for m in ds.metas if m["episode_index"]==old)
        lineage.append({"episode_index":new,"source_episode_index":old,**evidence.get(old,{"session_id":None,"combo":None}),
                        "source_fingerprint":evidence.get(old,{}).get("source_fingerprint",ds.fingerprint(source_meta)),
                        "derived_fingerprint":derived.fingerprint(derived.metas[new])})
        checks.append(sample_hashes(ds,source_meta)==sample_hashes(derived,derived.metas[new]))
    # Recompute numeric statistics from retained rows, not reused global originals.
    arrays={key:np.array([r[key] for m in derived.metas for r in derived.rows(m)],float) for key in ("action","observation.state")}
    stats=read_json(output/"meta/stats.json")
    for key,a in arrays.items():
        stats[key]={"min":a.min(0).tolist(),"max":a.max(0).tolist(),"mean":a.mean(0).tolist(),"std":a.std(0).tolist(),"count":[len(a)]}
    # Only files in this newly created output are updated.
    (output/"meta/stats.json").write_text(json.dumps(stats,indent=2),encoding="utf-8")
    provenance={"schema_version":1,"source_binding":ds.binding,"derived_binding":derived.binding,"episodes":lineage,
        "video_policy":"whole compressed files copied; unused source frames retained; original PTS intervals",
        "statistics_scope":"retained dataset numeric stats; NOT train-only; image stats aggregate retained episode stats"}
    write_new(output/"provenance.json",provenance); write_new(output/"excluded_episodes.json",plan)
    validation=audit(output,official_source=official_source,loader_python=loader_python)
    unchanged=before==tree_hash(ds.root)
    ok=(len(derived.metas)==len(ds.metas)-len(exclude) and all(checks) and unchanged
        and derived.info["total_frames"]==sum(ds.metas[i]["length"] for i in keep)
        and not validation["summary"]["FAIL"] and validation["reload"]["status"]=="PASS")
    result={"status":"PASS" if ok else "FAIL","source_unchanged":unchanged,"sample_checksums_equal":all(checks),
            "counts_match":len(derived.metas)==len(keep),"audit_summary":validation["summary"],"reload":validation["reload"]}
    write_new(output/"DERIVATION_CHECK.json",result)
    write_new(output/"DATASET_AUDIT.json",validation)
    if not ok: raise RecordError("derived validation failed; new output retained, not training-ready")
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("root","audit","out"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--reviews",type=Path); p.add_argument("--sessions",type=Path)
    mode=p.add_mutually_exclusive_group(); mode.add_argument("--dry-run",action="store_true"); mode.add_argument("--execute",action="store_true")
    p.add_argument("--official-source",type=Path); p.add_argument("--loader-python")
    a=p.parse_args(argv); result=build(a.root,read_json(a.audit),a.reviews,a.out,a.execute,a.official_source,a.loader_python,a.sessions)
    print(json.dumps({"status":result["status"],"excluded":len(result.get("plan",{}).get("excluded_episodes",[]))}))
    return 0


if __name__=="__main__": raise SystemExit(main())
