#!/usr/bin/env python3
"""Default read-only planning; explicit execute uses the guarded local-only adapter."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import re
import shlex
import sys
import yaml

from check_vla_cameras import UniqueLoader, load_config as camera_config
from vla_data import Dataset, ROOT, sha, tree_hash, now, outside
from vla_record import RecordError, read_json, digest
from vla_audit_dataset import units_evidence
from vla_split_sessions import split as make_split, session_evidence, validate_split
from vla_train_data import train_statistics

FIELDS={'schema_version','dataset_root','repo_id','split','sessions','audit','audit_receipt','cameras_config',
        'units_evidence','calibration_bounds','model','model_config','official_source','verified_options','output_dir',
        'seed','batch_size','steps','save_freq','num_workers','device','min_free_vram_bytes','wandb','push_to_hub','rename_map','physical_mapping_status','physical_mapping_evidence'}
PATHS={'dataset_root','split','sessions','audit','audit_receipt','cameras_config','units_evidence','calibration_bounds',
       'model_config','official_source','verified_options','output_dir','physical_mapping_evidence'}


def load_config(path):
    data=yaml.load(Path(path).read_text(),Loader=UniqueLoader)
    if not isinstance(data,dict) or set(data)!=FIELDS: raise RecordError('train config fields mismatch; secrets/extra options not accepted')
    if type(data['schema_version']) is not int or data['schema_version']!=1 or data['model']!='lerobot/smolvla_base': raise RecordError('unsupported schema/model')
    for k in ('seed','batch_size','steps','save_freq','num_workers','min_free_vram_bytes'):
        if type(data[k]) is not int or data[k]<(0 if k in ('seed','num_workers') else 1): raise RecordError('invalid integer setting')
    if data['device']!='cuda' or data['wandb'] is not False or data['push_to_hub'] is not False: raise RecordError('Stage4 requires cuda plan, tracking/hub off')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',data['repo_id']): raise RecordError('invalid repo identifier')
    for k in PATHS:
        if data[k] is not None:
            p=Path(data[k]); data[k]=str((ROOT/p).resolve() if not p.is_absolute() else p.resolve())
    return data


def get_rename_map(config, dataset_keys=None, model_keys=None):
    """Single accessor for training and future inference; no copied view assignments."""
    value=config['rename_map']
    expected={'observation.images.'+r for r in ('wrist','top','oblique')}
    if not isinstance(value,dict) or set(value)!=expected or len(set(value.values()))!=3: raise RecordError('missing/duplicate/reversed camera mapping')
    if not all(re.fullmatch(r'observation\.images\.camera[123]',v) for v in value.values()): raise RecordError('wrong model camera direction/keys')
    if dataset_keys is not None and not set(value)<=set(dataset_keys): raise RecordError('dataset camera missing')
    if model_keys is not None and not set(value.values())<=set(model_keys): raise RecordError('model input camera missing')
    return dict(value)


def repo_sha(root=ROOT):
    git=root/'.git'
    if git.is_file(): git=(root/git.read_text().strip().split(': ',1)[1]).resolve()
    head=(git/'HEAD').read_text().strip()
    if not head.startswith('ref: '): return head
    ref=head[5:]; path=git/ref
    if path.is_file(): return path.read_text().strip()
    for line in (git/'packed-refs').read_text().splitlines():
        if not line.startswith(('#','^')) and line.endswith(' '+ref): return line.split()[0]
    raise RecordError('git revision unavailable')


def verified_options(config):
    evidence=read_json(config['verified_options']); source=Path(config['official_source'])
    if evidence['source_version']!='0.6.0' or evidence['help_exit']!=0: raise RecordError('unverified CLI source')
    for relative,value in evidence['files'].items():
        path=source/relative
        if source not in path.resolve().parents or sha(path)!=value: raise RecordError('official source changed')
    return set(evidence['verified_flags'])|set(evidence['source_only_flags'])


def command(config,train_ids,rename,resume=None):
    args=[sys.executable,'-m','lerobot.scripts.lerobot_train']
    values={'dataset.repo_id':config['repo_id'],'dataset.root':config['dataset_root'],
        'dataset.episodes':json.dumps(train_ids,separators=(',',':')),'dataset.eval_split':'0.0',
        'dataset.use_imagenet_stats':'false','dataset.video_backend':'pyav','policy.device':config['device'],
        'policy.push_to_hub':'false','output_dir':str(Path(config['output_dir'])/'train'),'seed':str(config['seed']),
        'steps':str(config['steps']),'batch_size':str(config['batch_size']),'num_workers':str(config['num_workers']),
        'save_freq':str(config['save_freq']),'save_checkpoint_to_hub':'false','wandb.enable':'false',
        'env_eval_freq':'0','eval_steps':'0','job.target':'local','dataset.image_transforms.enable':'false',
        'rename_map':json.dumps(rename,separators=(',',':'))}
    if resume: values.update(resume='true',config_path=str(Path(resume).resolve()))
    else: values['policy.path']=str(Path(config['model_config']).parent) if config['model_config'] else config['model']
    allowed=verified_options(config)
    if any('--'+k not in allowed for k in values): raise RecordError('command contains unverified option')
    return args+['--'+k+'='+v for k,v in values.items()]


def resume_check(config, checkpoint, previous, contract):
    checkpoint=Path(checkpoint).resolve(); old=read_json(previous); saved=read_json(checkpoint)
    if old.get('contract')!=contract: raise RecordError('resume manifest mismatch')
    old_output=Path(old['output_dir']).resolve()
    if old_output not in checkpoint.parents or old_output==Path(config['output_dir']): raise RecordError('resume requires preserved old checkpoint and NEW output')
    for key in ('seed','steps','batch_size','num_workers','save_freq'):
        if saved.get(key)!=config[key]: raise RecordError('checkpoint training config mismatch')
    if saved.get('rename_map')!=get_rename_map(config): raise RecordError('checkpoint rename mismatch')
    d=saved.get('dataset',{})
    if d.get('root')!=config['dataset_root'] or d.get('repo_id')!=config['repo_id'] or d.get('episodes')!=contract['train_episodes'] or d.get('eval_split',0)!=0: raise RecordError('checkpoint dataset mismatch')
    policy=saved.get('policy',{})
    if policy.get('type')!='smolvla' or policy.get('push_to_hub') is not False: raise RecordError('checkpoint policy mismatch')
    if saved.get('job',{}).get('target') not in (None,'local') or saved.get('env') is not None or saved.get('reward_model') is not None:
        raise RecordError('remote/environment/reward resume prohibited')
    return {'checkpoint_config_sha256':sha(checkpoint),'previous_manifest_sha256':sha(previous)}


def prepare(config,*,accept_unit_unknown=False,reason=None,gpu_provider=None,resume=None,previous_manifest=None):
    """Read-only. gpu_provider is INTERNAL injectable evidence, never a CLI JSON bypass."""
    blockers=[]; info={}; ds=None; train_ids=[]; stats={}; rename=get_rename_map(config)
    if accept_unit_unknown and (not isinstance(reason,str) or not reason.strip() or len(reason)>400 or re.search(r'(hf_|ghp_|sk-|token\s*=|password\s*=)',reason,re.I)):
        raise RecordError('unit override requires a non-secret explanatory reason')
    if not accept_unit_unknown and reason: raise RecordError('unit reason requires explicit override')
    output=Path(config['output_dir'])
    if output.exists(): blockers.append('OUTPUT_ALREADY_EXISTS')
    outside(config['dataset_root'],output)
    try:
        ds=Dataset(config['dataset_root']); provenance=read_json(ds.root/'provenance.json')
        if provenance.get('derived_binding')!=ds.binding or provenance.get('source_binding',{}).get('root')==str(ds.root): raise RecordError('new derived provenance required')
        source=Dataset(provenance['source_binding']['root'])
        outside(source.root,output)
        if source.binding!=provenance['source_binding']: raise RecordError('original source metadata changed')
        source_metas={m['episode_index']:m for m in source.metas}
        lineage=provenance['episodes']
        if len({e['source_episode_index'] for e in lineage})!=len(lineage): raise RecordError('duplicate source remapping')
        for row in lineage:
            if row['source_episode_index'] not in source_metas or row['source_fingerprint']!=source.fingerprint(source_metas[row['source_episode_index']]):
                raise RecordError('source/derived index basis or fingerprint mismatch')
        evidence=session_evidence(ds,config['sessions'])
        partition=read_json(config['split']); validate_split(partition)
        regenerated=make_split(ds,evidence,partition['eval_ratio'],partition['seed'])
        if partition!=regenerated: raise RecordError('split content/identity/remapping/session mismatch')
        train_ids=partition['train_episodes']
        if not train_ids or not partition['eval_episodes']: raise RecordError('explicit nonempty train/eval sessions required')
        report=read_json(config['audit']); receipt=read_json(config['audit_receipt'])
        if report['dataset_binding']!=ds.binding or receipt.get('dataset_binding')!=ds.binding or receipt.get('audit_sha256')!=sha(config['audit']) or receipt.get('tree_sha256')!=digest(tree_hash(ds.root)):
            raise RecordError('stale/swapped audit or source contents')
        if report.get('criteria',{}).get('video_mode')!='full' or report.get('reload',{}).get('status')!='PASS': raise RecordError('full video audit and official reload required')
        indexed={e['episode_index']:e for e in report['episodes']}
        if len(indexed)!=len(ds.metas) or set(indexed)!={m['episode_index'] for m in ds.metas}: raise RecordError('audit index basis mismatch')
        if any(indexed[i]['status']=='FAIL' or any(r['severity']=='FAIL' for r in indexed[i]['reasons']) for i in train_ids): raise RecordError('audit FAIL included in train')
        model=read_json(config['model_config']) if config['model_config'] else None
        if model is None:
            blockers.append('LOCAL_BASE_MODEL_CONFIG_MISSING')
        else:
            get_rename_map(config,ds.camera_keys,model['input_features'])
            if model.get('type')!='smolvla' or model.get('normalization_mapping')!={'VISUAL':'IDENTITY','STATE':'MEAN_STD','ACTION':'MEAN_STD'}: raise RecordError('base model normalization not supported by numeric-only adapter')
            info['model_config_sha256']=sha(config['model_config'])
            if Path(config['model_config']).name!='config.json': raise RecordError('base metadata must be local model directory/config.json')
            model_dir=Path(config['model_config']).parent
            weights=model_dir/'model.safetensors'
            if not weights.is_file() or weights.stat().st_size==0:
                blockers.append('LOCAL_MODEL_WEIGHTS_MISSING')
            else: info['local_weights_bytes']=weights.stat().st_size
        unit=units_evidence(ds,config['units_evidence'],config['calibration_bounds'])
        if unit['status']=='UNKNOWN' and not accept_unit_unknown: blockers.append('UNITS_UNVERIFIED')
        stats=train_statistics(ds,train_ids)
        info.update(dataset_binding=ds.binding,meta_info_sha256=sha(ds.root/'meta/info.json'),
            split_sha256=sha(config['split']),audit_sha256=sha(config['audit']),audit_receipt_sha256=sha(config['audit_receipt']),
            provenance_sha256=sha(ds.root/'provenance.json'),source_binding=provenance['source_binding'],
            train_episodes=train_ids,eval_episodes=partition['eval_episodes'],train_stats=stats,
            train_stats_sha256=digest(stats),unit_status=unit['status'],
            unit_evidence_sha256=sha(config['units_evidence']) if config['units_evidence'] else None)
    except (OSError,KeyError,ValueError,TypeError) as exc:
        blockers.append('DATA_PREFLIGHT:'+type(exc).__name__+':'+str(exc)[:180])
    try:
        camera_config(config['cameras_config']); info['cameras_config_sha256']=sha(config['cameras_config'])
        if config['physical_mapping_status']!='VERIFIED_FROM_RECORDING' or not config['physical_mapping_evidence'] or ds is None:
            blockers.append('PHYSICAL_CAMERA_MAPPING_UNVERIFIED')
        else:
            physical=read_json(config['physical_mapping_evidence'])
            if physical['cameras_config_sha256']!=info['cameras_config_sha256'] or physical['dataset_binding']!=ds.binding or physical['rename_map']!=rename or not physical['evidence']:
                raise RecordError('camera evidence binding mismatch')
            for ref in physical['evidence']:
                if ref['line']<1 or sha(ref['file'])!=ref['sha256'] or ref['contains'] not in Path(ref['file']).read_text().splitlines()[ref['line']-1]:
                    raise RecordError('camera file/line evidence mismatch')
    except (OSError,ValueError,KeyError,IndexError): blockers.append('CAMERA_CONFIG_OR_EVIDENCE_INVALID')
    try: args=command(config,train_ids,rename,resume)
    except (OSError,KeyError,ValueError): args=[]; blockers.append('OFFICIAL_OPTIONS_UNVERIFIED')
    # No GPU query during dry-run. Execution supplies a fresh read-only query.
    gpu=gpu_provider() if gpu_provider else None
    if not gpu or gpu.get('available') is not True or gpu.get('free_bytes',0)<config['min_free_vram_bytes']:
        blockers.append('GPU_VRAM_UNVERIFIED')
    contract={k:info.get(k) for k in ('dataset_binding','split_sha256','meta_info_sha256','audit_receipt_sha256','provenance_sha256','source_binding','train_episodes','eval_episodes','train_stats_sha256','model_config_sha256','cameras_config_sha256','unit_evidence_sha256')}
    contract.update(model=config['model'],seed=config['seed'],batch_size=config['batch_size'],steps=config['steps'],num_workers=config['num_workers'],save_freq=config['save_freq'],rename_sha256=digest(rename),source_options_sha256=sha(config['verified_options']))
    resume_info={}
    if bool(resume)!=bool(previous_manifest): blockers.append('RESUME_REQUIRES_CONFIG_AND_MANIFEST')
    elif resume:
        try: resume_info=resume_check(config,resume,previous_manifest,contract)
        except (OSError,KeyError,ValueError): blockers.append('RESUME_IDENTITY_MISMATCH')
    try: installed=importlib.metadata.version('lerobot')
    except importlib.metadata.PackageNotFoundError: installed='NOT_INSTALLED'
    manifest={'schema_version':1,'status':'BLOCKED' if blockers else 'PREFLIGHT_PASS','repo_sha':repo_sha(),'installed_lerobot_version':installed,
        'planned_source_version':'0.6.0','source_options_sha256':sha(config['verified_options']),
        'train_config_sha256':digest(config),'rename_sha256':digest(rename),'output_dir':str(output),
        'contract':contract,'seed':config['seed'],'prepared_at':now(),'start_time':None,'command_args':args,
        'command_display':shlex.join(args),'command_status':'CANDIDATE_ONLY_NOT_EXECUTABLE',
        'actual_launch_args':None,'verified_upstream_candidate_args':args,
        'adapter_callable':'vla_train_launch.execute','adapter_status':'IMPLEMENTED_LIVE_TRAINING_NOT_RUN',
        'gpu_evidence':gpu,'memory_fit_status':'NOT_RUN_ADMISSION_FLOOR_ONLY',
        'planned_child_environment':{'PYTHONPATH':str(Path(config['official_source'])/'src'),'HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','WANDB_MODE':'disabled'},
        'credential_policy':'environment variable names only; secret values never read into manifest',
        'shell':False,'unit_unknown_override':{'accepted':accept_unit_unknown,'reason':reason if accept_unit_unknown else None},
        'resume':resume_info,'blockers':blockers,**info}
    return manifest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--config',type=Path,default=ROOT/'configs/vla_train.yaml')
    modes=p.add_mutually_exclusive_group(); modes.add_argument('--dry-run',action='store_true'); modes.add_argument('--execute',action='store_true')
    p.add_argument('--accept-unit-unknown',action='store_true'); p.add_argument('--unit-unknown-reason')
    p.add_argument('--resume-config',type=Path); p.add_argument('--previous-manifest',type=Path)
    a=p.parse_args(argv)
    if a.execute:
        from vla_train_launch import execute
        return execute(a.config,accept_unit_unknown=a.accept_unit_unknown,reason=a.unit_unknown_reason,
                       resume=a.resume_config,previous_manifest=a.previous_manifest)
    result=prepare(load_config(a.config),accept_unit_unknown=a.accept_unit_unknown,reason=a.unit_unknown_reason,resume=a.resume_config,previous_manifest=a.previous_manifest)
    result['train_config_file_sha256']=sha(a.config)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__': raise SystemExit(main())
