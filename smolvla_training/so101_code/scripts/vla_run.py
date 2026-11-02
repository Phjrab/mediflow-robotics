#!/usr/bin/env python3
"""Default readiness only. --mode mock --out NEW writes synthetic logs only."""
import argparse,json,sys,time,uuid
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from medicine_sorter.vla_policy_adapter import (MockPolicy,MockStreamingController,UnitContract,Limits,
    readiness,run_mock_loop,load_config,JOINTS)
from check_vla_cameras import load_config as cameras_config
from vla_record import load_tasks,digest
from vla_train import get_rename_map,repo_sha
from vla_data import sha,write_new


class SyntheticProvider:
    physical=False
    def __init__(self,controller,cameras,clock=time.monotonic): self.controller=controller;self.cameras=cameras;self.clock=clock
    def read(self,task):
        ts=self.clock()
        return dict(state=self.controller.state.copy(),state_timestamp=ts,temperatures=np.array([25.]*6),
            overload=False,stop_requested=False,task=task,frames={role:dict(timestamp=ts,image=np.zeros((c['height'],c['width'],3),dtype=np.uint8)) for role,c in self.cameras['cameras'].items()})


def synthetic_units():
    return UnitContract(JOINTS.copy(),'degree','degree','percent','percent','synthetic','synthetic',[[-90,90]]*5+[[0,100]],'SYNTHETIC_FIXTURE')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=['plan','mock','shadow','live'],default='plan')
    p.add_argument('--execute',action='store_true');p.add_argument('--first-run',action='store_true')
    p.add_argument('--shadow-sim',action='store_true');p.add_argument('--task');p.add_argument('--allow-unseen-task',action='store_true')
    p.add_argument('--out',type=Path);p.add_argument('--steps',type=int,default=4)
    a=p.parse_args(argv)
    if a.mode!='mock':
        print(json.dumps(readiness(a.mode,a.execute),ensure_ascii=False,indent=2));return 2
    if a.out is None or a.out.exists(): p.error('mock output must be a NEW directory')
    c=load_config(ROOT/'configs/vla_train.yaml');cams=cameras_config(ROOT/'configs/vla_cameras.yaml')
    task=a.task or load_tasks(ROOT/'configs/vla_tasks.yaml')['combinations']['A:red']['ko'][0]
    state=np.array([0.,0.,0.,0.,0.,50.]);controller=MockStreamingController(state)
    provider=SyntheticProvider(controller,cams);units=synthetic_units();binding={'root':'SYNTHETIC_ONLY','metadata_sha256':'synthetic'}
    evidence=dict(dataset_binding=binding,audit_status='PASS',unit_status='SYNTHETIC_FIXTURE',checked_at=time.monotonic(),mean=state.tolist(),tolerance=[1.]*6)
    policy=MockPolicy([np.tile(state,(2,1)) for _ in range(a.steps)],2)
    result=run_mock_loop(policy,provider,controller,train_config=c,units=units,
        limits=Limits(cap_deg=.5 if a.first_run else 2),meta_info={'fps':30},task=task,start_evidence=evidence,
        dataset_binding=binding,max_steps=a.steps,max_time=2 if a.first_run else 5,
        shadow_sim=a.shadow_sim,allow_unseen=a.allow_unseen_task)
    a.out.mkdir(parents=True,exist_ok=False);run_id=str(uuid.uuid4())
    manifest=dict(run_id=run_id,mode=result['mode'],repo_sha=repo_sha(),checkpoint_sha256=None,checkpoint_status='MOCK_NO_MODEL',
        train_config_sha256=sha(ROOT/'configs/vla_train.yaml'),camera_config_sha256=sha(ROOT/'configs/vla_cameras.yaml'),
        rename_map=get_rename_map(c),rename_sha256=digest(get_rename_map(c)),task=result['task'],units=vars(units),
        proof_status='SYNTHETIC_ONLY_NOT_LIVE_AUTHORIZATION',first_run=a.first_run,video_saved=False,automatic_success=None)
    write_new(a.out/'manifest.json',manifest)
    with (a.out/'steps.jsonl').open('x') as f:
        for row in result['records']: f.write(json.dumps(dict(run_id=run_id,**row),allow_nan=False)+'\n')
    write_new(a.out/'result.json',{k:v for k,v in result.items() if k!='records'})
    print(json.dumps({'status':result['mode'],'steps':len(result['records']),'reason':result['reason'],'out':str(a.out.resolve())}));return 0 if result['outcome']=='BOUNDED_LOOP_FINISHED' else 2


if __name__=='__main__':raise SystemExit(main())
