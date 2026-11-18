"""동일 진입점. dry-run은 실제 serial을 절대로 열지 않는다."""
import argparse
import json
import os
import sys
from pathlib import Path
from config_runtime import load

def legacy_live_with_metadata(function, args, fps):
    """Annotate only this invocation's receipt, without changing the legacy loop."""
    import tempfile
    previous = os.environ.get('SO101_RUN_RECEIPT')
    if previous is None:
        runs = Path(__file__).resolve().parents[1] / 'runs'
        runs.mkdir(exist_ok=True)
        receipt = Path(tempfile.mkdtemp(prefix='ENTRY_', dir=runs))
        os.environ['SO101_RUN_RECEIPT'] = str(receipt)
    else:
        receipt = Path(previous)
    try:
        return function(args.duration,supported_home=args.release,speed=args.speed,
                        web_corrections=args.web_corrections,taught_forward=args.taught_forward)
    finally:
        try:
            pointer = receipt / 'run_path'
            if pointer.exists():
                run = Path(pointer.read_text(encoding='utf-8').strip()).resolve()
                runs = Path(__file__).resolve().parents[1] / 'runs'
                if runs.resolve() not in run.parents:
                    raise ValueError('run receipt outside runs directory')
                summary = run / 'summary.json'
                if summary.exists():
                    value = json.loads(summary.read_text(encoding='utf-8'))
                    value.update(mode='legacy',fps=fps,speed=args.speed,requested_speed=args.speed)
                    temporary = run / 'summary.mode.tmp'
                    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
                    os.replace(temporary,summary)
        finally:
            if previous is None:
                os.environ.pop('SO101_RUN_RECEIPT',None)

def main():
    p=argparse.ArgumentParser();c=load()
    modes=p.add_mutually_exclusive_group()
    for name in ['live','dry-run','mock','simulate','policy-check','offline-eval','camera-check']:modes.add_argument('--'+name,action='store_true')
    modes.add_argument('--recorded-replay',type=Path)
    p.add_argument('--execute',action='store_true');p.add_argument('--operator-present',action='store_true')
    p.add_argument('--duration',type=float,default=c['duration']);p.add_argument('--speed',type=float,default=c['speed'])
    p.add_argument('--release',action='store_true')
    p.add_argument('--web-corrections',action='store_true')
    p.add_argument('--taught-forward',type=Path)
    p.add_argument('--mode',choices=('legacy','raw'),default='legacy')
    args=p.parse_args()
    if not 0<args.speed<=1 or not 0<args.duration<=3600:p.error('속도·시간 범위 오류')
    import stream_live
    stream_live.DEFAULT_SPEED=args.speed
    # 홈 복귀도 실행 인자의 재생 속도를 따른다.
    import live_start
    live_start.ACTIVE_SPEED=args.speed
    print(json.dumps({'mode':args.mode,'fps':c['model_hz'],
                      'speed':1.0 if args.mode=='raw' else args.speed,
                      'requested_speed':args.speed},ensure_ascii=False),flush=True)
    if args.mode=='raw' and (args.web_corrections or args.taught_forward or args.recorded_replay):
        p.error('raw 모드는 정책 FIFO 실행 전용입니다; 웹 교정·녹화 재생은 legacy를 사용하세요')
    if args.recorded_replay:
        if not args.execute or not args.operator_present:p.error('녹화 실기는 --execute --operator-present 필요')
        os.environ['SO101_DIRECT_OPERATOR']='1'
        from recorded_replay import run_operator
        return run_operator(args.recorded_replay,args.speed)
    if args.mock:
        import unittest
        pattern='test_raw_mode.py' if args.mode=='raw' else 'test_unified.py'
        suite=unittest.defaultTestLoader.discover(str(Path(__file__).parents[1]/'sim'),pattern=pattern)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.policy_check:
        from trial_runtime import static_context
        stream_live.make_policy(static_context()).close()
        return 0
    if args.offline_eval:
        from offline_check import main as evaluate
        return evaluate()
    if args.simulate:
        if args.mode=='raw':
            from raw_live import run_simulations
            return run_simulations(args.duration,args.speed)
        from startup_simulation import run_simulations
        return run_simulations(args.duration,args.speed)
    if args.live:
        os.environ['SO101_DIRECT_OPERATOR']='1'
        if args.mode=='raw':
            from raw_live import live
            return live(args.duration,supported_home=args.release,speed=args.speed)
        return legacy_live_with_metadata(stream_live.live,args,c['model_hz'])
    # 프로세스 전체에서 실물 serial open을 차단: 카메라·모델만 실제다.
    def deny_serial(event,values):
        if event=='open' and values and isinstance(values[0],(str,bytes,os.PathLike)):
            s=os.fsdecode(values[0])
            if s.startswith(('/dev/ttyACM','/dev/ttyUSB','/dev/serial/')):raise RuntimeError('dry-run serial 열기 차단')
    sys.addaudithook(deny_serial)
    if args.camera_check:
        from camera_check import main as capture
        return capture()
    if args.mode=='raw':
        from raw_live import run_dry
        return run_dry(args.duration,args.speed)
    from startup_simulation import run_dry
    return run_dry(args.duration,args.speed)

if __name__=='__main__':raise SystemExit(main())
