import copy
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import yaml
import numpy as np
from vla_fixture import fixture,mutate
from vla_data import Dataset,ROOT,sha,tree_hash,write_new
from vla_record import RecordError,new_counter,digest
from vla_sessions import new_run
from vla_split_sessions import session_evidence,split
from vla_audit_dataset import audit
from vla_train import load_config,get_rename_map,prepare,resume_check,main
from vla_train_data import train_statistics,partitioned_datasets,invoke_training
from vla_compare_act_cmd import historical,compare
from vla_eval_offline import chunk_mae


class TrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.original=Dataset(fixture(self.base/'original'));self.ds=Dataset(fixture(self.base/'derived'))
        self.config=load_config(ROOT/'configs/vla_train.yaml')
        for key,name in dict(dataset_root='derived',split='split.json',sessions='sessions.jsonl',audit='audit.json',audit_receipt='receipt.json',model_config='config.json',output_dir='new-training',physical_mapping_evidence='physical.json').items():
            self.config[key]=str(self.base/name)
        rows=[];counter=new_counter(self.original.root,'local/test');counter['actual_repo_id']='local/test';lineage=[]
        for m in self.ds.metas:
            i=m['episode_index'];combo='A:red' if i%2==0 else 'B:blue';fingerprint=self.ds.fingerprint(m)
            rows.append(dict(schema_version=1,episode_index=i,dataset_binding=dict(root=str(self.original.root),repo_id='local/test'),counter_path=str(self.base/'counter.json'),
                **new_run('session'+str(i)),combo=combo,task=m['tasks'][0],camera_config_sha256='a'*64,episode_fingerprint=fingerprint))
            counter['episodes'].append(dict(index=i,combo=combo,task=m['tasks'][0],fingerprint=fingerprint));counter['counts'][combo]+=1
            lineage.append(dict(episode_index=i,source_episode_index=i,session_id='session'+str(i),combo=combo,source_fingerprint=fingerprint,derived_fingerprint=fingerprint))
        counter['revision']=len(rows);write_new(self.base/'counter.json',counter)
        (self.base/'sessions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        write_new(self.ds.root/'provenance.json',dict(schema_version=1,source_binding=self.original.binding,derived_binding=self.ds.binding,episodes=lineage))
        partition=split(self.ds,session_evidence(self.ds,self.base/'sessions.jsonl'),.34,101);write_new(self.base/'split.json',partition)
        model=dict(type='smolvla',input_features={v:{} for v in get_rename_map(self.config).values()},normalization_mapping={'VISUAL':'IDENTITY','STATE':'MEAN_STD','ACTION':'MEAN_STD'})
        write_new(self.base/'config.json',model)
        self.refresh_audit()

    def refresh_audit(self):
        report=audit(self.ds.root); report['reload']={'status':'PASS','scope':'MOCK_ONLY'}
        Path(self.config['audit']).write_text(json.dumps(report))
        receipt=dict(dataset_binding=Dataset(self.ds.root).binding,audit_sha256=sha(self.config['audit']),tree_sha256=digest(tree_hash(self.ds.root)))
        Path(self.config['audit_receipt']).write_text(json.dumps(receipt))

    def result(self,**kwargs): return prepare(self.config,**kwargs)

    def test_unknown_units_blocked(self): self.assertIn('UNITS_UNVERIFIED',self.result()['blockers'])
    def test_override_requires_reason(self):
        for reason in (None,'','  ','token=secret'):
            with self.assertRaises(RecordError): self.result(accept_unit_unknown=True,reason=reason)
    def test_override_records_reason_not_other_gates(self):
        result=self.result(accept_unit_unknown=True,reason='synthetic unknown unit trial')
        self.assertNotIn('UNITS_UNVERIFIED',result['blockers']);self.assertIn('GPU_VRAM_UNVERIFIED',result['blockers'])
        self.assertEqual(result['unit_unknown_override']['reason'],'synthetic unknown unit trial')
    def test_execution_blocked_by_real_missing_evidence(self):
        result=self.result(accept_unit_unknown=True,reason='synthetic',gpu_provider=lambda:dict(available=True,free_bytes=99999999999,fit_verified=True))
        self.assertIn('PHYSICAL_CAMERA_MAPPING_UNVERIFIED',result['blockers'])
        self.assertIn('LOCAL_MODEL_WEIGHTS_MISSING',result['blockers'])
        self.assertFalse(any(b.startswith('EXECUTION_DISABLED') for b in result['blockers']))
    def test_missing_camera_mapping(self):
        del self.config['rename_map']['observation.images.wrist']
        with self.assertRaises(RecordError): get_rename_map(self.config)
    def test_duplicate_mapping(self):
        keys=list(self.config['rename_map']);self.config['rename_map'][keys[0]]=self.config['rename_map'][keys[1]]
        with self.assertRaises(RecordError): get_rename_map(self.config)
    def test_reversed_mapping(self):
        self.config['rename_map']={v:k for k,v in self.config['rename_map'].items()}
        with self.assertRaises(RecordError): get_rename_map(self.config)
    def test_missing_model_camera(self):
        with self.assertRaises(RecordError): get_rename_map(self.config,self.ds.camera_keys,[])
    def test_model_metadata_absent_blocked(self):
        self.config['model_config']=None
        self.assertIn('LOCAL_BASE_MODEL_CONFIG_MISSING',self.result()['blockers'])
    def test_source_remapping_mismatch(self):
        p=self.ds.root/'provenance.json';v=json.loads(p.read_text());v['episodes'][0]['source_episode_index']=500;p.write_text(json.dumps(v))
        self.assertTrue(any('index basis' in b for b in self.result()['blockers']))
    def test_source_data_changed(self):
        mutate(self.original.root,'rows',lambda r:r[0]['action'].__setitem__(0,500))
        self.assertTrue(any('fingerprint mismatch' in b for b in self.result()['blockers']))
    def test_stale_audit_hash(self):
        p=Path(self.config['audit']);p.write_text(p.read_text()+' ')
        self.assertTrue(any('stale/swapped' in b for b in self.result()['blockers']))
    def test_swapped_audit(self):
        p=Path(self.config['audit']);v=json.loads(p.read_text());v['dataset_binding']['root']='wrong';p.write_text(json.dumps(v))
        self.assertTrue(any('stale/swapped' in b for b in self.result()['blockers']))
    def test_fail_train_rejected(self):
        p=Path(self.config['audit']);v=json.loads(p.read_text());idx=json.loads(Path(self.config['split']).read_text())['train_episodes'][0]
        v['episodes'][idx]['status']='FAIL';p.write_text(json.dumps(v));r=Path(self.config['audit_receipt']);receipt=json.loads(r.read_text());receipt['audit_sha256']=sha(p);r.write_text(json.dumps(receipt))
        self.assertTrue(any('FAIL included' in b for b in self.result()['blockers']))
    def test_split_overlap(self):
        p=Path(self.config['split']);v=json.loads(p.read_text());v['eval_episodes']=v['train_episodes'];p.write_text(json.dumps(v))
        self.assertTrue(any('DATA_PREFLIGHT' in b for b in self.result()['blockers']))
    def test_unknown_session_not_allowed_into_training(self):
        p=Path(self.config['split']);v=json.loads(p.read_text());v['unknown_episodes']=v['train_episodes'];p.write_text(json.dumps(v))
        self.assertTrue(any('DATA_PREFLIGHT' in b for b in self.result()['blockers']))
    def test_new_output_required(self):
        Path(self.config['output_dir']).mkdir();self.assertIn('OUTPUT_ALREADY_EXISTS',self.result()['blockers'])
    def test_manifest_and_no_secret_values(self):
        with patch.dict(os.environ,{'HF_TOKEN':'hf_private_secret','WANDB_API_KEY':'secret_wandb'}): result=self.result()
        for key in ('repo_sha','installed_lerobot_version','meta_info_sha256','split_sha256','cameras_config_sha256','train_config_sha256','rename_sha256','command_args','seed','prepared_at','unit_unknown_override'):self.assertIn(key,result)
        self.assertIsNone(result['start_time']);self.assertNotIn('hf_private_secret',json.dumps(result));self.assertNotIn('secret_wandb',json.dumps(result))
    def test_dry_run_no_side_effect_import_or_subprocess(self):
        before=tree_hash(self.base)
        import builtins
        original=builtins.__import__
        def guard(name,*args,**kwargs):
            if name.split('.')[0] in ('torch','transformers','requests','socket'):raise AssertionError('forbidden import '+name)
            return original(name,*args,**kwargs)
        with patch('subprocess.Popen',side_effect=AssertionError('subprocess')),patch('builtins.__import__',side_effect=guard):self.result()
        self.assertEqual(before,tree_hash(self.base))
    def test_cli_execute_does_not_launch(self):
        p=self.base/'train.yaml';p.write_text(yaml.safe_dump(self.config))
        with patch('subprocess.Popen',side_effect=AssertionError('no launch')),patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(main(['--config',str(p),'--execute']),2)
        self.assertFalse(Path(self.config['output_dir']).exists())
    def test_extra_secret_config_rejected(self):
        c=copy.deepcopy(self.config);c['token']='secret';p=self.base/'invalid.yaml';p.write_text(yaml.safe_dump(c))
        with self.assertRaises(RecordError):load_config(p)
    def test_train_only_stats_excludes_eval(self):
        stats=train_statistics(self.ds,[0]);expected=np.array([r['action'] for r in self.ds.rows(self.ds.metas[0])]).mean(0)
        np.testing.assert_allclose(stats['action']['mean'],expected)
        self.assertNotEqual(stats['action']['mean'],train_statistics(self.ds,[0,1,2])['action']['mean'])
    def test_injected_adapter_preserves_partitions_and_stats(self):
        cfg=SimpleNamespace(dataset=SimpleNamespace(episodes=None,eval_split=.5,use_imagenet_stats=True));calls=[]
        def factory(c):calls.append(c);return SimpleNamespace(meta=SimpleNamespace(stats={'leaked_eval':True}))
        stats=train_statistics(self.ds,[0,2]);train,ev=partitioned_datasets(cfg,factory,[0,2],[1],stats)
        self.assertEqual([c.dataset.episodes for c in calls],[[0,2],[1]]);self.assertTrue(all(c.dataset.eval_split==0 for c in calls))
        self.assertNotIn('leaked_eval',ev.meta.stats);np.testing.assert_equal(train.meta.stats['action']['mean'],ev.meta.stats['action']['mean'])
    def test_eval_change_does_not_change_train_stats(self):
        expected=train_statistics(self.ds,[0,2])
        mutate(self.ds.root,'rows',lambda rows:[r['action'].__setitem__(0,9000) for r in rows if r['episode_index']==1])
        self.assertEqual(expected,train_statistics(Dataset(self.ds.root),[0,2]))
    def test_injected_training_uses_protected_data_and_restores_hook(self):
        cfg=SimpleNamespace(dataset=SimpleNamespace(episodes=None,eval_split=.2,use_imagenet_stats=True))
        sentinel=lambda _:None
        module=SimpleNamespace(make_train_eval_datasets=sentinel)
        module.train=lambda c:module.make_train_eval_datasets(c)
        factory=lambda c:SimpleNamespace(meta=SimpleNamespace(stats={'global':True}),episodes=c.dataset.episodes)
        stats=train_statistics(self.ds,[0,2])
        train,ev=invoke_training(cfg,module,factory,[0,2],[1],stats)
        self.assertEqual(train.episodes,[0,2]);self.assertEqual(ev.episodes,[1]);self.assertNotIn('global',train.meta.stats)
        self.assertIs(module.make_train_eval_datasets,sentinel)
    def test_resume_requires_matching_config_manifest(self):
        result=self.result();old=self.base/'old';ckpt=old/'checkpoints/10/train_config.json';ckpt.parent.mkdir(parents=True)
        manifest=dict(result,output_dir=str(old));previous=self.base/'manifest.json';write_new(previous,manifest)
        saved={k:self.config[k] for k in ('seed','steps','batch_size','num_workers','save_freq')}
        saved.update(rename_map=get_rename_map(self.config),dataset=dict(root=self.config['dataset_root'],repo_id=self.config['repo_id'],episodes=result['contract']['train_episodes'],eval_split=0),policy=dict(type='smolvla',push_to_hub=False))
        write_new(ckpt,saved);self.assertIn('checkpoint_config_sha256',resume_check(self.config,ckpt,previous,result['contract']))
        saved['seed']+=1;ckpt.write_text(json.dumps(saved))
        with self.assertRaises(RecordError):resume_check(self.config,ckpt,previous,result['contract'])
    def test_resume_wrong_manifest(self):
        p=self.base/'prev.json';write_new(p,{'contract':{}});q=self.base/'ckpt.json';write_new(q,{})
        with self.assertRaises(RecordError):resume_check(self.config,q,p,self.result()['contract'])
    def test_act_nine_combo_rejected(self):
        script=self.base/'act.sh';script.write_text('--steps=100000\n--batch_size=8\n--num_workers=2\n')
        with self.assertRaises(RecordError):compare(self.config,script)
    def test_act_extract_only_and_missing_script(self):
        script=self.base/'act.sh';script.write_text('--steps=100000\n--batch_size=8\n--num_workers=2\n')
        result=historical(script);self.assertEqual(result['values']['batch_size'],8);self.assertIn('not explicit',result['chunk_size'])
        self.assertEqual(historical(self.base/'missing')['status'],'NOT_RUN')
    def test_chunk_boundary_mask(self):
        result=chunk_mae(np.zeros((2,3,6)),np.ones((4,6)),[0,0,1,1],[1,3])
        self.assertEqual(result['valid_pairs'],2);self.assertEqual(result['horizon_counts'],[2,0,0]);self.assertEqual(result['joint_mae'],[1]*6)
    def test_chunk_nonfinite_rejected(self):
        with self.assertRaises(RecordError):chunk_mae(np.full((1,2,6),np.nan),np.ones((3,6)),[0]*3,[0])


if __name__=='__main__':unittest.main()
