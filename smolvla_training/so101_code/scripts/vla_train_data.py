"""Pure CPU train-only statistics and injectable dataset adapter. No policy imports."""
from copy import deepcopy
import numpy as np
from vla_record import RecordError


def train_statistics(ds, indices):
    selected=set(indices)
    if not selected or len(selected)!=len(indices): raise RecordError("nonempty unique training indices required")
    rows=[r for m in ds.metas if m['episode_index'] in selected for r in ds.rows(m)]
    stats={}
    for key in ('action','observation.state'):
        values=np.asarray([r[key] for r in rows],dtype=float)
        if values.ndim!=2 or values.shape[1]!=6 or not np.isfinite(values).all(): raise RecordError('invalid train numeric values')
        stats[key]={k:v.tolist() for k,v in dict(min=values.min(0),max=values.max(0),mean=values.mean(0),std=values.std(0),count=np.array([len(values)])).items()}
    return stats


def partitioned_datasets(cfg, factory, train_ids, eval_ids, stats):
    """Adapter contract tested with fake loaders. Live training wiring is NOT_RUN.

    SmolVLA VISUAL=IDENTITY; no corpus image statistics are used. Numeric stats
    are computed from train rows only and shared with eval, never eval-derived.
    """
    if not train_ids or set(train_ids)&set(eval_ids): raise RecordError('partition overlap/empty train')
    if set(stats)!={'action','observation.state'}: raise RecordError('unexpected normalization features')
    result=[]
    for ids in (train_ids,eval_ids):
        if not ids:
            result.append(None); continue
        local=deepcopy(cfg); local.dataset.episodes=list(ids); local.dataset.eval_split=0.0
        local.dataset.use_imagenet_stats=False
        dataset=factory(local)
        dataset.meta.stats={k:{s:np.asarray(v) for s,v in values.items()} for k,values in stats.items()}
        result.append(dataset)
    return tuple(result)


def invoke_training(cfg, training_module, dataset_factory, train_ids, eval_ids, stats):
    """Dependency-injected wiring, tested ONLY with fake training_module.

    No module imports or launch here; caller must pass verified official objects.
    The public launcher uses the same partition adapter through upstream main().
    """
    original=training_module.make_train_eval_datasets
    training_module.make_train_eval_datasets=lambda requested:partitioned_datasets(requested,dataset_factory,train_ids,eval_ids,stats)
    try:
        return training_module.train(cfg)
    finally:
        training_module.make_train_eval_datasets=original
