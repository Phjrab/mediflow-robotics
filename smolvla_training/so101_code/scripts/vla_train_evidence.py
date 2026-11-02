#!/usr/bin/env python3
"""Explicit CPU audit + immutable content receipt; never a policy/model operation."""
import argparse
from pathlib import Path
from vla_data import Dataset,tree_hash,new_output,write_new,sha,now
from vla_record import digest,RecordError
from vla_audit_dataset import audit


def seal(root,out,official_source=None):
    ds=Dataset(root); before=tree_hash(ds.root); folder=new_output(root,out)
    report=audit(root,video_mode='full',official_source=official_source)
    if tree_hash(ds.root)!=before: raise RecordError('source changed during audit')
    write_new(folder/'DATASET_AUDIT.json',report)
    receipt=dict(schema_version=1,dataset_binding=ds.binding,audit_sha256=sha(folder/'DATASET_AUDIT.json'),
                 tree_sha256=digest(before),created_at=now(),scope='full source file hashes and full video audit')
    write_new(folder/'content_receipt.json',receipt)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
    p.add_argument('--official-source',type=Path)
    a=p.parse_args(); report=seal(a.root,a.out,a.official_source)
    print(report['summary'],report['reload']['status'])
