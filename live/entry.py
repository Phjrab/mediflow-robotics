"""동일 진입점. dry-run은 실제 serial을 절대로 열지 않는다."""
import argparse
import json
import os
import sys
from pathlib import Path
from config_runtime import load

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
    args=p.parse_args()
    if not 0<args.speed<=1 or not 0<args.duration<=3600:p.error('속도·시간 범위 오류')
    import stream_live
    stream_live.DEFAULT_SPEED=args.speed
    # 홈 복귀도 실행 인자의 재생 속도를 따른다.
    import live_start
    live_start.ACTIVE_SPEED=args.speed
    if args.recorded_replay:
        if not args.execute or not args.operator_present:p.error('녹화 실기는 --execute --operator-present 필요')
        os.environ['SO101_DIRECT_OPERATOR']='1'
        from recorded_replay import run_operator
        return run_operator(args.recorded_replay,args.speed)
    if args.mock:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(Path(__file__).parents[1]/'sim'),pattern='test_unified.py')
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.policy_check:
        from trial_runtime import static_context
        stream_live.make_policy(static_context()).close()
        return 0
    if args.offline_eval:
        from offline_check import main as evaluate
        return evaluate()
    if args.simulate:
        from startup_simulation import run_simulations
        return run_simulations(args.duration,args.speed)
    if args.live:
        os.environ['SO101_DIRECT_OPERATOR']='1'
        return stream_live.live(args.duration,supported_home=args.release,speed=args.speed,web_corrections=args.web_corrections,taught_forward=args.taught_forward)
    # 프로세스 전체에서 실물 serial open을 차단: 카메라·모델만 실제다.
    def deny_serial(event,values):
        if event=='open' and values and isinstance(values[0],(str,bytes,os.PathLike)):
            s=os.fsdecode(values[0])
            if s.startswith(('/dev/ttyACM','/dev/ttyUSB','/dev/serial/')):raise RuntimeError('dry-run serial 열기 차단')
    sys.addaudithook(deny_serial)
    if args.camera_check:
        from camera_check import main as capture
        return capture()
    from startup_simulation import run_dry
    return run_dry(args.duration,args.speed)

if __name__=='__main__':raise SystemExit(main())
