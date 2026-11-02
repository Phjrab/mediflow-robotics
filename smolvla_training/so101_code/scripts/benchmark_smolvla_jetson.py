#!/usr/bin/env python3
"""Plan/mock by default; --measure is an explicit future on-site GPU benchmark."""
import argparse,json,resource,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from medicine_sorter.vla_policy_adapter import SmolVLAWrapper,rename_observation
from vla_train import load_config
from check_vla_cameras import load_config as cameras_config
from vla_data import write_new
from vla_record import load_tasks


def summary(latencies,n,fps,*,synthetic=False):
    if type(n) is not int or n<=0 or isinstance(fps,bool) or not np.isfinite(fps) or fps<=0: raise ValueError('invalid timing contract')
    if not latencies: return {'status':'NOT_RUN','p50_s':None,'p95_s':None,'max_s':None,'effective_control_hz':None,'delay_consumption_ratio':None}
    values=np.asarray(latencies,dtype=float)
    if not np.isfinite(values).all() or (values<=0).any() or n<=0 or fps<=0: raise ValueError('invalid timings')
    p50,p95=np.percentile(values,[50,95]);duration=n/fps
    return dict(status='SYNTHETIC_NOT_JETSON' if synthetic else 'MEASURED_SPEED_ONLY',p50_s=float(p50),p95_s=float(p95),max_s=float(values.max()),
        consumption_s=duration,delay_consumption_ratio=float(p95/duration),effective_control_hz=float(n/(p50+duration)),
        effective_hz_definition='sequential blocking n/(latency+n/fps), not measured motor Hz',
        timing_candidate='SYNC_CANDIDATE_NOT_CONTINUITY_PROOF' if values.max()<duration else 'RTC_OR_ASYNC_REQUIRED',
        task_performance='NOT_EVALUATED')


def classify_error(error):
    s=str(error).lower()
    if 'out of memory' in s: return 'OOM'
    if isinstance(error,(ImportError,ModuleNotFoundError)): return 'MISSING_EXTRAS'
    if any(x in s for x in ('cuda','driver','bfloat16')): return 'TORCH_CUDA_OR_DTYPE'
    if any(x in s for x in ('checkpoint','cache','offline','network','download')): return 'LOCAL_WEIGHTS_OR_NETWORK_BLOCKED'
    return 'OTHER_LOAD_OR_RUNTIME_ERROR'


def benchmark(predict,obs,*,n,fps,warmup=2,repeats=5,sync=lambda:None,clock=time.perf_counter):
    if type(warmup) is not int or type(repeats) is not int or warmup<0 or repeats<1: raise ValueError('invalid repeats')
    elapsed=[]
    for i in range(warmup+repeats):
        sync();start=clock();predict(obs);sync();dt=clock()-start
        if i>=warmup: elapsed.append(dt)
    return summary(elapsed,n,fps)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group();modes.add_argument('--mock',action='store_true');modes.add_argument('--measure',action='store_true')
    p.add_argument('--policy-path','--checkpoint',dest='policy_path',default='lerobot/smolvla_base');p.add_argument('--source',type=Path);p.add_argument('--out',type=Path)
    p.add_argument('--warmup',type=int,default=2);p.add_argument('--repeats',type=int,default=5);p.add_argument('--n-action-steps',type=int,default=50);p.add_argument('--fps',type=float,default=30)
    a=p.parse_args(argv)
    if a.out and a.out.exists():p.error('new output required')
    if not 1<=a.n_action_steps<=50 or not np.isfinite(a.fps) or a.fps<=0 or a.warmup<0 or a.repeats<1:p.error('invalid timing contract')
    report={'policy_path':a.policy_path,'checkpoint':a.policy_path if Path(a.policy_path).is_dir() else None,
        'power_clocks':'UNKNOWN; automatic power/clocks probe not implemented','task_performance':'NOT_EVALUATED','results':{}}
    for dtype in ('fp32','bf16','fp16'):
        r=summary([],a.n_action_steps,a.fps);r.update(gpu_peak_bytes=None,rss_bytes=None,available_gpu_bytes=None,available_ram_bytes=None)
        if a.mock:
            r.update(summary([.1,.15,.2],a.n_action_steps,a.fps,synthetic=True))
        elif a.measure:
            if not Path(a.policy_path).is_dir() or not a.source:p.error('measure requires local checkpoint and pinned source; Hub identifiers never downloaded')
            model=None
            try:
                # No camera/provider/robot imports: generated dummy pixels only.
                cameras=cameras_config(ROOT/'configs/vla_cameras.yaml');cfg=load_config(ROOT/'configs/vla_train.yaml')
                obs={'observation.state':np.array([0.,0.,0.,0.,0.,50.]),'task':load_tasks(ROOT/'configs/vla_tasks.yaml')['combinations']['A:red']['ko'][0]}
                obs.update({'observation.images.'+k:np.zeros((v['height'],v['width'],3),np.uint8) for k,v in cameras['cameras'].items()})
                model=SmolVLAWrapper(Path(a.policy_path),a.source,dtype=dtype,n_action_steps=a.n_action_steps)
                torch=model.torch;torch.cuda.reset_peak_memory_stats()
                r.update(benchmark(model.predict,rename_observation(obs,cfg),n=a.n_action_steps,fps=a.fps,warmup=a.warmup,repeats=a.repeats,sync=torch.cuda.synchronize))
                r.update(gpu_peak_bytes=int(torch.cuda.max_memory_allocated()),available_gpu_bytes=int(torch.cuda.mem_get_info()[0]),
                    rss_bytes=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024),available_ram_bytes=next(int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
            except Exception as exc:r.update(status='FAILED',failure_type=classify_error(exc))
            finally:
                if model is not None:
                    del model
                    import gc;gc.collect()
                    torch.cuda.empty_cache()
        report['results'][dtype]=r
    if a.out:
        a.out.parent.mkdir(parents=True,exist_ok=True);write_new(a.out,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
