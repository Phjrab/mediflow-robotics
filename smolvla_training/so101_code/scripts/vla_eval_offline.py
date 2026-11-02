"""Mock-only action chunk MAE. No checkpoint loading and no robot success claims."""
import numpy as np
from vla_record import RecordError


def chunk_mae(predicted,target,episode_ids,start_indices):
    pred=np.asarray(predicted,float); truth=np.asarray(target,float)
    if pred.ndim!=3 or pred.shape[2]!=6 or truth.ndim!=2 or truth.shape[1]!=6 or len(start_indices)!=len(pred) or len(episode_ids)!=len(truth):
        raise RecordError('chunk/target/index dimensions mismatch')
    if not np.isfinite(pred).all() or not np.isfinite(truth).all(): raise RecordError('nonfinite action')
    total=np.zeros(6); count=0; horizon_sum=np.zeros((pred.shape[1],6)); horizon_count=np.zeros(pred.shape[1],int)
    for row,start in enumerate(start_indices):
        if type(start) is not int or not 0<=start<len(truth): raise RecordError('invalid chunk start')
        for h in range(pred.shape[1]):
            j=start+h
            if j>=len(truth) or episode_ids[j]!=episode_ids[start]: break
            error=np.abs(pred[row,h]-truth[j]);total+=error;count+=1;horizon_sum[h]+=error;horizon_count[h]+=1
    return dict(joint_mae=(total/count).tolist() if count else None,valid_pairs=count,
        horizon_mae=[(s/n).tolist() if n else None for s,n in zip(horizon_sum,horizon_count)],
        horizon_counts=horizon_count.tolist(),interpretation='Offline action error, NOT robot success rate; native units require evidence',actual_checkpoint_evaluation='NOT_RUN')
