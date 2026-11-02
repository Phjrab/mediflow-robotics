"""Fake upstream.main + real data-only official source fixture. No model training."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import yaml
import numpy as np
import test_vla_stage4 as fixture_module
from vla_data import ROOT,sha,write_new,tree_hash
from vla_train import prepare,command
from vla_train_launch import execute,invoke_official_main


class LaunchTests(unittest.TestCase):
    setUp=fixture_module.TrainTests.setUp
    refresh_audit=fixture_module.TrainTests.refresh_audit

    def ready(self):
        # These bytes are NOT a real model, only evidence-gate fixture input.
        (self.base/'model.safetensors').write_bytes(b'MOCK_NEVER_LOADED')
        self.config['physical_mapping_status']='VERIFIED_FROM_RECORDING'
        camera=Path(self.config['cameras_config'])
        write_new(self.base/'physical.json',dict(cameras_config_sha256=sha(camera),dataset_binding=self.ds.binding,
            rename_map=self.config['rename_map'],evidence=[dict(file=str(camera),line=1,contains=camera.read_text().splitlines()[0],sha256=sha(camera))]))
        path=self.base/'train.yaml';path.write_text(yaml.safe_dump(self.config))
        return path

    def gpu(self): return dict(available=True,free_bytes=12*1024**3)

    def manifest(self):
        return prepare(self.config,accept_unit_unknown=True,reason='synthetic',gpu_provider=self.gpu)

    def fake(self,remote=False,explode=False):
        c=self.config;m=self.manifest();calls=[]
        cfg=NS(job=NS(is_remote=remote,target='remote' if remote else 'local'),env=None,reward_model=None,env_eval_freq=0,eval_steps=0,
            wandb=NS(enable=False),save_checkpoint_to_hub=False,output_dir=str(Path(c['output_dir'])/'train'),rename_map=c['rename_map'],
            dataset=NS(root=c['dataset_root'],repo_id=c['repo_id'],episodes=m['train_episodes'],eval_split=0,use_imagenet_stats=False,streaming=False,image_transforms=NS(enable=False)),
            policy=NS(type='smolvla',push_to_hub=False,normalization_mapping={'VISUAL':'IDENTITY','STATE':'MEAN_STD','ACTION':'MEAN_STD'}),**{k:c[k] for k in ('seed','steps','batch_size','num_workers','save_freq')})
        upstream=NS(make_train_eval_datasets=lambda _:None)
        def train(config):
            calls.append(list(sys.argv))
            assert not Path(config.output_dir).exists(), 'wrapper created upstream output too soon'
            train,ev=upstream.make_train_eval_datasets(config)
            self.assertEqual(train.episodes,m['train_episodes']);self.assertEqual(ev.episodes,m['eval_episodes'])
            np.testing.assert_allclose(ev.meta.stats['action']['mean'],m['train_stats']['action']['mean'])
            self.assertEqual(os.environ['HF_HUB_OFFLINE'],'1')
            if explode: raise RuntimeError('raw secret exception must not enter report')
        upstream.train=train;upstream.main=lambda:upstream.train(cfg)
        factory=lambda local:NS(episodes=local.dataset.episodes,meta=NS(stats={'global':True}))
        return (upstream,factory,lambda:lambda fn:fn,type(cfg)),calls

    def test_all_gates_pass_without_permanent_block(self):
        self.ready();m=self.manifest();self.assertEqual(m['blockers'],[]);self.assertEqual(m['status'],'PREFLIGHT_PASS')

    def test_fake_main_argv_stats_manifest_and_new_output(self):
        path=self.ready();objects,calls=self.fake();original=objects[0].train;sentinel=objects[0].make_train_eval_datasets
        self.assertEqual(execute(path,accept_unit_unknown=True,reason='synthetic',_gpu=self.gpu,_load=lambda _:objects),0)
        manifest=json.loads((Path(self.config['output_dir'])/'run_manifest.json').read_text())
        self.assertIs(objects[0].train,original);self.assertIs(objects[0].make_train_eval_datasets,sentinel)
        self.assertIn('--job.target=local',calls[0]);self.assertIn('--dataset.eval_split=0.0',calls[0])
        self.assertEqual(calls[0][1:],manifest['verified_upstream_candidate_args'][3:])
        self.assertIsNotNone(manifest['start_time']);self.assertIn('--unit-unknown-reason',manifest['actual_launch_args'])
        self.assertFalse((Path(self.config['output_dir'])/'train').exists())

    def test_remote_rejected_before_upstream_train(self):
        path=self.ready();objects,calls=self.fake(remote=True)
        self.assertEqual(execute(path,accept_unit_unknown=True,reason='synthetic',_gpu=self.gpu,_load=lambda _:objects),1)
        self.assertEqual(calls,[])

    def test_failure_restores_hooks_and_redacts_exception(self):
        path=self.ready();objects,calls=self.fake(explode=True);original=objects[0].train
        self.assertEqual(execute(path,accept_unit_unknown=True,reason='synthetic',_gpu=self.gpu,_load=lambda _:objects),1)
        self.assertIs(objects[0].train,original)
        result=(Path(self.config['output_dir'])/'run_result.json').read_text();self.assertNotIn('raw secret',result)

    def test_gpu_floor_blocks_before_loader(self):
        path=self.ready()
        with patch('sys.stdout',new_callable=io.StringIO):
            rc=execute(path,accept_unit_unknown=True,reason='synthetic',_gpu=lambda:dict(available=True,free_bytes=1),_load=lambda _:self.fail('loader called'))
        self.assertEqual(rc,2);self.assertFalse(Path(self.config['output_dir']).exists())

    def test_preflight_rechecks_evidence(self):
        path=self.ready()
        def changed():
            p=Path(self.config['audit']);p.write_text(p.read_text()+' ');return self.gpu()
        with patch('sys.stdout',new_callable=io.StringIO):
            rc=execute(path,accept_unit_unknown=True,reason='synthetic',_gpu=changed,_load=lambda _:self.fail('loader called'))
        self.assertEqual(rc,2)

    def test_resume_no_policy_path_and_old_checkpoint_preserved(self):
        path=self.ready();m=self.manifest();old=self.base/'old/train';ckpt=old/'checkpoints/100/pretrained_model/train_config.json';ckpt.parent.mkdir(parents=True)
        saved={k:self.config[k] for k in ('seed','steps','batch_size','num_workers','save_freq')}
        saved.update(rename_map=self.config['rename_map'],dataset=dict(root=self.config['dataset_root'],repo_id=self.config['repo_id'],episodes=m['train_episodes'],eval_split=0),policy=dict(type='smolvla',push_to_hub=False))
        write_new(ckpt,saved);previous=self.base/'previous.json';write_new(previous,dict(m,output_dir=str(old.parent)))
        before=tree_hash(old);objects,calls=self.fake()
        self.assertEqual(execute(path,accept_unit_unknown=True,reason='synthetic',resume=ckpt,previous_manifest=previous,_gpu=self.gpu,_load=lambda _:objects),0)
        self.assertIn('--resume=true',calls[0]);self.assertFalse(any(a.startswith('--policy.path=') for a in calls[0]))
        self.assertIn('--config_path='+str(ckpt),calls[0]);self.assertEqual(before,tree_hash(old))

    @unittest.skipUnless(os.environ.get('VLA_OFFICIAL_SOURCE'),'official source not provided')
    def test_official_dataset_partitions_stats_no_training(self):
        result=self.base/'official-partitions.json';source=os.environ['VLA_OFFICIAL_SOURCE']
        env=dict(os.environ,PYTHONPATH=str(Path(source)/'src'),HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',HF_DATASETS_CACHE=str(self.base/'hf-datasets'))
        p=subprocess.run([sys.executable,str(ROOT/'scripts/vla_official_worker.py'),'--mode','partitions','--root',str(self.ds.root),'--source',source,'--result',str(result)],env=env,capture_output=True,text=True,timeout=90)
        self.assertEqual(p.returncode,0,result.read_text() if result.exists() else p.stderr[-1500:])
        report=json.loads(result.read_text());self.assertEqual(report['train_episodes'],[0,2]);self.assertEqual(report['eval_episodes'],[1]);self.assertEqual(report['training'],'NOT_RUN')


if __name__=='__main__':unittest.main()
