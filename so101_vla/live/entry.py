"""동일 진입점. dry-run은 실제 serial을 절대로 열지 않는다."""
import argparse
import json
import os
import sys
from pathlib import Path
from config_runtime import load

def legacy_live_with_metadata(function, args, fps, **options):
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
                        web_corrections=args.web_corrections,taught_forward=args.taught_forward,**options)
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
    p.add_argument('--policy', choices=('smolvla', 'act'), default='smolvla')
    p.add_argument('--act-checkpoint', type=Path)
    act_source = p.add_mutually_exclusive_group()
    act_source.add_argument('--act-recorded-inputs', type=Path, help='사전 추출한 세 카메라의 RGB 사용')
    act_source.add_argument('--act-alignment-preview', action='store_true', help='실행 중인 정렬 서비스의 최신 3카메라 JPEG를 읽기만 함')
    p.add_argument('--n-action-steps', type=int, choices=(25, 50, 100))
    p.add_argument('--replay', type=int, metavar='EPISODE', help='선택된 시연 action을 정책 없이 한 번 재생')
    p.add_argument('--vel-limit', choices=('data',), help='학습 p99 속도에 재생 배율을 적용')
    p.add_argument('--assist-grasp', action='store_true', help='집기 직전 자세를 최대 5도 보조')
    p.add_argument('--reject-stale-home', action='store_true', help='이전 홈 관측에서 나온 지연 복귀 목표를 폐기하고 재추론')
    p.add_argument('--precollect', action='store_true', help='홈 검사 전에 Python 전체 메모리 회수를 한 번 수행')
    p.add_argument('--smooth-reversals', action='store_true', help='어깨 들기와 팔꿈치의 짧은 목표 반전을 최대 11틱 완화')
    args=p.parse_args()
    if not 0<args.speed<=1 or not 0<args.duration<=3600:p.error('속도·시간 범위 오류')
    if args.policy == 'act':
        if (not args.dry_run or args.mode != 'legacy' or args.act_checkpoint is None
                or args.execute or args.operator_present or args.replay is not None or args.release
                or args.web_corrections or args.taught_forward or args.vel_limit or args.assist_grasp
                or args.reject_stale_home or args.precollect or args.smooth_reversals):
            p.error('ACT 사전 검증은 --mode legacy --dry-run --act-checkpoint 경로로 실행하세요')
        from act_dry import run
        return run(args)
    if args.act_checkpoint or args.act_recorded_inputs or args.act_alignment_preview or args.n_action_steps is not None:
        p.error('ACT 전용 인자에는 --policy act가 필요합니다')
    options = {}
    if args.smooth_reversals:
        options['smooth_reversals'] = True
    if args.precollect:
        options['precollect'] = True
    if args.reject_stale_home:
        options['reject_stale_home'] = True
    if args.vel_limit:
        options['vel_limit'] = args.vel_limit
    if args.assist_grasp:
        options['assist_grasp'] = True
    if options and (args.mode != 'legacy' or args.replay is not None or args.recorded_replay
                    or args.mock or args.policy_check or args.offline_eval or args.camera_check
                    or args.web_corrections or args.taught_forward):
        p.error('속도 제한과 집기 보조는 legacy 정책 실행·모의·dry-run에서 사용하세요')
    if args.replay is not None:
        if (args.mode != 'legacy' or args.recorded_replay or args.mock or args.simulate or args.policy_check
                or args.offline_eval or args.camera_check or args.web_corrections or args.taught_forward or args.release):
            p.error('시연 재생은 단독 실행 모드입니다')
        if args.speed not in (.33, .5) or not 0 <= args.replay <= 136 or args.duration > 180:
            p.error('시연 번호(0~136), 속도(0.33 또는 0.5), 시간(최대 180초)을 확인하세요')
        if args.live and not (args.execute and args.operator_present):
            p.error('실물 녹화 재생은 --live --execute --operator-present 필요')
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
        return run_simulations(args.duration,args.speed,**options)
    if args.live:
        os.environ['SO101_DIRECT_OPERATOR']='1'
        if args.replay is not None:
            from episode_replay import run_episode
            return run_episode(args.replay, args.duration, args.speed, dry_run=False)
        if args.mode=='raw':
            from raw_live import live
            return live(args.duration,supported_home=args.release,speed=args.speed)
        return legacy_live_with_metadata(stream_live.live,args,c['model_hz'],**options)
    # 프로세스 전체에서 실물 serial open을 차단: 카메라·모델만 실제다.
    def deny_serial(event,values):
        if event=='open' and values and isinstance(values[0],(str,bytes,os.PathLike)):
            s=os.fsdecode(values[0])
            if s.startswith(('/dev/ttyACM','/dev/ttyUSB','/dev/serial/')):raise RuntimeError('dry-run serial 열기 차단')
    sys.addaudithook(deny_serial)
    if args.replay is not None:
        from episode_replay import run_episode
        return run_episode(args.replay, args.duration, args.speed, dry_run=True)
    if args.camera_check:
        from camera_check import main as capture
        return capture()
    if args.mode=='raw':
        from raw_live import run_dry
        return run_dry(args.duration,args.speed)
    from startup_simulation import run_dry
    return run_dry(args.duration,args.speed,**options)

if __name__=='__main__':raise SystemExit(main())
