#!/usr/bin/env python3
"""Comparison command drafts only. Never execute/source a historical shell script."""
import argparse
import json
from pathlib import Path
import re
import shlex
from vla_train import load_config,prepare
from vla_data import Dataset,sha
from vla_record import RecordError
from vla_split_sessions import session_evidence


def historical(path):
    p=Path(path)
    if not p.is_file(): return {'status':'NOT_RUN','reason':'historical ACT script unavailable'}
    lines=p.read_text().splitlines(); values={}; evidence={}
    for i,line in enumerate(lines,1):
        for name in ('batch_size','steps','num_workers','chunk_size'):
            match=re.search(r'--'+name+r'=(\d+)',line)
            if match: values[name]=int(match.group(1)); evidence[name]={'file':str(p.resolve()),'line':i,'text':line.strip()}
    return dict(status='READ_ONLY',source_sha256=sha(p),values=values,evidence=evidence,
        cameras='not explicit in shell script; derive from selected dataset features',
        chunk_size='not explicit in shell script; do not infer old runtime default')


def compare(config,script,**kwargs):
    original=historical(script)
    if original['status']=='NOT_RUN': return original
    ds=Dataset(config['dataset_root']); evidence=session_evidence(ds,config['sessions'])
    if set(evidence)!={m['episode_index'] for m in ds.metas} or {e['combo'] for e in evidence.values()}!={'A:red'}:
        raise RecordError('ACT language-free comparison requires confirmed A:red-only dataset, not nine combinations')
    plan=prepare(config,**kwargs); args=plan['command_args']
    if not args: raise RecordError('verified command unavailable')
    act=[a for a in args if not a.startswith(('--policy.path=','--rename_map='))]
    act.append('--policy.type=act')
    act=[('--output_dir='+str(Path(config['output_dir']).with_name(Path(config['output_dir']).name+'-act'))) if a.startswith('--output_dir=') else a for a in act]
    return dict(status='CANDIDATES_ONLY_NOT_EXECUTABLE',historical=original,smolvla=args,act=act,
        smolvla_display=shlex.join(args),act_display=shlex.join(act),blockers=plan['blockers'],
        caveat='Same data/episodes/seed/steps/batch; ACT has no language conditioning. Neither draft injects train-only stats without the validated adapter.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--act-script',type=Path,required=True)
    a=p.parse_args();print(json.dumps(compare(load_config(a.config),a.act_script),ensure_ascii=False,indent=2))
