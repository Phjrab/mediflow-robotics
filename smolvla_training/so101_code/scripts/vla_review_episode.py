#!/usr/bin/env python3
"""Contact sheets and append-only human verdicts, outside original dataset."""
import argparse
import csv
import io
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

from vla_data import Dataset, samples, new_output, outside, now
from vla_record import RecordError
from vla_sessions import ledger_lock, atomic_append_bytes

FIELDS = ["dataset_root","metadata_sha256","episode_index","verdict","reason","reviewer","date"]


def history(path, binding):
    if path is None or not Path(path).exists():
        return []
    with Path(path).open(newline="",encoding="utf-8") as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise RecordError("invalid review schema")
        rows=list(reader)
    for r in rows:
        if r["verdict"] not in ("ok","fail","hold") or not r["reason"].strip() or not r["reviewer"].strip():
            raise RecordError("invalid review record")
        int(r["episode_index"])
    # A shared review file can contain several datasets. Never mix index 0s.
    return [r for r in rows if r["dataset_root"]==binding["root"] and r["metadata_sha256"]==binding["metadata_sha256"]]


def latest_reviews(path,binding):
    return {int(r["episode_index"]):r for r in history(path,binding)} # append order wins


def append_review(ds,episode,path,verdict,reason,reviewer):
    if verdict not in ("ok","fail","hold") or not reason.strip() or not reviewer.strip() or episode not in [m["episode_index"] for m in ds.metas]:
        raise RecordError("valid episode/verdict/reason/reviewer required")
    if Path(path).is_symlink():
        raise RecordError("review symlink rejected")
    path=outside(ds.root,path)
    with ledger_lock(path):
        history(path,ds.binding)
        out=io.StringIO(newline="")
        writer=csv.DictWriter(out,fieldnames=FIELDS)
        if not path.exists() or path.stat().st_size==0:
            writer.writeheader()
        writer.writerow(dict(dataset_root=str(ds.root),metadata_sha256=ds.binding["metadata_sha256"],episode_index=episode,
            verdict=verdict,reason=reason,reviewer=reviewer,date=now()))
        atomic_append_bytes(path,out.getvalue().encode())


def render(ds,episode,out,count=5,plot=False):
    if not 3<=count<=30:
        raise RecordError("representative frame count must be 3..30")
    meta=next(m for m in ds.metas if m["episode_index"]==episode)
    frames=samples(ds,meta,count)
    folder=new_output(ds.root,out)
    columns=max(len(v) for v in frames.values())
    sheet=Image.new("RGB",(columns*240,len(frames)*210+45),"white")
    draw=ImageDraw.Draw(sheet); draw.text((8,8),f"Episode {episode} | start / representative / end | review, not success proof",fill="black")
    for row,(key,images) in enumerate(frames.items()):
        for col,(idx,array) in enumerate(sorted(images.items())):
            image=Image.fromarray(array); image.thumbnail((230,170))
            x,y=col*240+5,row*210+45
            sheet.paste(image,(x,y+25)); draw.text((x,y),f"{key.split('.')[-1]} frame {idx}",fill="black")
    sheet.save(folder/"contact_sheet.png")
    if plot:
        rows=ds.rows(meta)
        image=Image.new("RGB",(900,600),"white"); draw=ImageDraw.Draw(image)
        colors=["red","blue","green","purple","orange","black"]
        for k,feature in enumerate(("observation.state","action")):
            values=np.asarray([r[feature] for r in rows],float)
            if not np.isfinite(values).all():
                raise RecordError("cannot plot nonfinite trajectory")
            lo,hi=float(values.min()),float(values.max()); span=max(hi-lo,1e-6)
            y0=30+k*300
            draw.text((10,y0),f"{feature}: native values (units require evidence); range {lo:.2f}..{hi:.2f}",fill="black")
            for joint,color in enumerate(colors):
                points=[(30+i*830/max(1,len(values)-1),y0+260-(v-lo)/span*230) for i,v in enumerate(values[:,joint])]
                if len(points)>1: draw.line(points,fill=color,width=2)
        image.save(folder/"state_action.png")
    return folder


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True); p.add_argument("--episode",type=int,required=True)
    p.add_argument("--out",type=Path,default=Path("reports/review")); p.add_argument("--frames",type=int,default=5)
    p.add_argument("--plot",action="store_true"); p.add_argument("--mark",choices=("ok","fail","hold"))
    p.add_argument("--reason"); p.add_argument("--reviewer"); p.add_argument("--reviews",type=Path,default=Path("data/vla_review.csv"))
    p.add_argument("--mark-only",action="store_true")
    a=p.parse_args(argv); ds=Dataset(a.root)
    if a.mark_only and not a.mark: p.error("--mark-only requires --mark")
    if a.mark and (not a.reason or not a.reviewer): p.error("mark requires reason and reviewer")
    if not a.mark_only: render(ds,a.episode,a.out,a.frames,a.plot)
    if a.mark: append_review(ds,a.episode,a.reviews,a.mark,a.reason,a.reviewer)
    print("review artifacts saved; original dataset unchanged")
    return 0


if __name__=="__main__": raise SystemExit(main())
