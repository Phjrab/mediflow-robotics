"""Explicit LOCAL training entrypoint. Never imported by default dry-run.

Live forward/backward is NOT_RUN during development. Unit tests inject a fake
upstream main; official dataset integration tests never import the trainer.
"""
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sys

from vla_data import now, sha, write_new
from vla_record import RecordError,read_json
from vla_train import load_config, prepare, verified_options
from vla_train_data import partitioned_datasets

OFFLINE={'HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
         'WANDB_MODE':'disabled','HF_HUB_DISABLE_TELEMETRY':'1'}


def gpu_evidence():
    """Read-only admission check, not a model-memory-fit guarantee."""
    import torch
    if not torch.cuda.is_available(): return {'available':False,'free_bytes':0}
    free,total=torch.cuda.mem_get_info()
    return {'available':True,'free_bytes':int(free),'total_bytes':int(total),'queried_at':now()}


@contextmanager
def offline_environment(cache=None):
    values=dict(OFFLINE)
    if cache is not None: values['HF_DATASETS_CACHE']=str(cache)
    previous={k:os.environ.get(k) for k in values}
    os.environ.update(values)
    try: yield
    finally:
        for k,v in previous.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v


def load_official(config):
    """Executed only AFTER all real gates pass. No source modification."""
    verified_options(config)
    source=(Path(config['official_source'])/'src').resolve()
    # Mixing a previously imported installed release with the pinned source is forbidden.
    for name,module in tuple(sys.modules.items()):
        if name=='lerobot' or name.startswith('lerobot.'):
            location=getattr(module,'__file__',None)
            if location and source not in Path(location).resolve().parents:
                raise RecordError('another lerobot version already imported; use a fresh process')
    sys.path.insert(0,str(source))
    from vla_official_worker import protect_source
    protect_source(Path(config['dataset_root']).resolve())
    provenance=read_json(Path(config['dataset_root'])/'provenance.json')
    protect_source(Path(provenance['source_binding']['root']).resolve())
    # The data tree and hardware/network guard remain in force for this process.
    trainer=importlib.import_module('lerobot.scripts.lerobot_train')
    factory=importlib.import_module('lerobot.datasets.factory').make_dataset
    parser=importlib.import_module('lerobot.configs.parser')
    config_type=importlib.import_module('lerobot.configs.train').TrainPipelineConfig
    if source not in Path(trainer.__file__).resolve().parents: raise RecordError('trainer source mismatch')
    return trainer,factory,parser.wrap,config_type


def validate_parsed(cfg,config,manifest):
    """Called before upstream train(), hence before its remote-dispatch branch."""
    if cfg.job.is_remote or getattr(cfg.job,'target',None) not in (None,'local'):
        raise RecordError('remote/cloud dispatch prohibited')
    if cfg.env is not None or cfg.reward_model is not None or cfg.env_eval_freq!=0 or cfg.eval_steps!=0:
        raise RecordError('only offline policy training supported')
    if cfg.wandb.enable or cfg.save_checkpoint_to_hub: raise RecordError('external logging/upload prohibited')
    if Path(cfg.output_dir).resolve()!=Path(config['output_dir'])/'train': raise RecordError('output override mismatch')
    if cfg.rename_map!=config['rename_map']: raise RecordError('parsed rename mismatch')
    for key in ('seed','steps','batch_size','num_workers','save_freq'):
        if getattr(cfg,key)!=config[key]: raise RecordError('parsed training setting mismatch')
    d=cfg.dataset
    if (Path(d.root).resolve()!=Path(config['dataset_root']) or d.repo_id!=config['repo_id']
        or d.episodes!=manifest['train_episodes'] or d.eval_split!=0 or d.use_imagenet_stats
        or d.streaming or d.image_transforms.enable): raise RecordError('parsed dataset mismatch')


def invoke_official_main(config,manifest,upstream,factory,wrap,config_type):
    """Version-verified module hook, no raw-CLI path around protected stats."""
    original_train=upstream.train; original_factory=upstream.make_train_eval_datasets
    original_argv=sys.argv
    def checked(cfg):
        validate_parsed(cfg,config,manifest)
        return original_train(cfg)
    checked.__annotations__={'cfg':config_type}
    def protected(cfg):
        validate_parsed(cfg,config,manifest)
        if cfg.policy.type!='smolvla' or cfg.policy.push_to_hub:
            raise RecordError('unexpected resolved policy')
        def value(v): return getattr(v,'value',v)
        norms={value(k):value(v) for k,v in cfg.policy.normalization_mapping.items()}
        if norms!={'VISUAL':'IDENTITY','STATE':'MEAN_STD','ACTION':'MEAN_STD'}:
            raise RecordError('resolved normalization differs from train-only adapter contract')
        return partitioned_datasets(cfg,factory,manifest['train_episodes'],manifest['eval_episodes'],manifest['train_stats'])
    upstream.train=wrap()(checked)
    upstream.make_train_eval_datasets=protected
    # Candidate prefix is python -m module; main receives only its verified flags.
    sys.argv=['lerobot-train']+manifest['verified_upstream_candidate_args'][3:]
    try:
        return upstream.main()
    finally:
        upstream.train=original_train;upstream.make_train_eval_datasets=original_factory
        sys.argv=original_argv


def execute(config_path,*,accept_unit_unknown=False,reason=None,resume=None,previous_manifest=None,
            _gpu=gpu_evidence,_load=load_official):
    """Public execute uses fresh preflight; injectable dependencies are NOT CLI options."""
    config=load_config(config_path)
    kwargs=dict(accept_unit_unknown=accept_unit_unknown,reason=reason,resume=resume,previous_manifest=previous_manifest)
    initial=prepare(config,**kwargs)
    # Do not even query CUDA if data/options/mapping/model evidence is missing.
    if any(b!='GPU_VRAM_UNVERIFIED' for b in initial['blockers']):
        print(json.dumps(initial,ensure_ascii=False,indent=2));return 2
    fresh_gpu=_gpu()
    result=prepare(config,gpu_provider=lambda:fresh_gpu,**kwargs)
    if result['blockers']:
        print(json.dumps(result,ensure_ascii=False,indent=2));return 2
    if result['contract']!=initial['contract']: raise RecordError('preflight evidence changed')
    result['train_config_file_sha256']=sha(config_path)
    launch_args=[sys.executable,str(Path(__file__).with_name('vla_train.py')),'--config',str(Path(config_path).resolve()),'--execute']
    if accept_unit_unknown: launch_args+=['--accept-unit-unknown','--unit-unknown-reason',reason]
    if resume: launch_args+=['--resume-config',str(Path(resume).resolve()),'--previous-manifest',str(Path(previous_manifest).resolve())]
    root=Path(config['output_dir'])
    root.mkdir(parents=True,exist_ok=False)
    result.update(status='STARTING',start_time=now(),actual_launch_args=launch_args,
                  upstream_output=str(root/'train'),execution_mode='protected_in_process_main',model_compute_status='NOT_YET_RUN')
    write_new(root/'run_manifest.json',result)
    try:
        with offline_environment(root/'dataset-cache'):
            upstream,factory,wrap,config_type=_load(config)
            invoke_official_main(config,result,upstream,factory,wrap,config_type)
        write_new(root/'run_result.json',{'status':'COMPLETED','ended_at':now()})
        return 0
    except BaseException as exc:
        # No raw exception text: it may contain credentials from third-party code.
        write_new(root/'run_result.json',{'status':'FAILED','error_type':type(exc).__name__,'ended_at':now()})
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__,'result':str(root/'run_result.json')}))
        return 1
