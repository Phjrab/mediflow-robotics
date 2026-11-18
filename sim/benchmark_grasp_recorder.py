"""Recorder scheduling benchmark with synthetic RGB and fake reads; no devices.

This is not evidence of Jetson serial latency or physical motor behavior.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import statistics
import time

from grasp_recorder import CAMERAS, GraspRecorder


JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return dict(count=0, mean=None, p50=None, p95=None, max=None)
    def q(fraction):
        index = fraction*(len(ordered)-1)
        lo, hi = math.floor(index), math.ceil(index)
        return ordered[lo]+(ordered[hi]-ordered[lo])*(index-lo)
    return dict(count=len(ordered), mean=statistics.mean(ordered), p50=q(.5), p95=q(.95), max=max(ordered))


def run_case(mode, output, seconds, pixels):
    recorder = None if mode == 'off' else GraspRecorder(output, JOINTS, backend=mode)
    captured, read_times, work_times, gaps, lateness = [], [], [], [], []
    main_cpu_start, started = time.process_time(), time.perf_counter()
    due = started
    previous = None
    steps = 0
    while True:
        time.sleep(max(0, due-time.perf_counter()))
        tick = time.perf_counter()
        if tick-started >= seconds:
            break
        if previous is not None:
            gaps.append(tick-previous)
        lateness.append(max(0, tick-due))
        previous = tick
        due = started+(steps+1)/60
        read_start = time.perf_counter()
        # Identical CPU-only fake read workload in every case. No serial import,
        # socket, robot object, calibration, camera, or hardware writes occur.
        checksum = 0
        for j in range(6):
            for k in range(64):
                checksum ^= (j*43+k*7) & 255
        observed = [float((checksum+j)%17) for j in range(6)]
        sample = dict(state=observed, present_raw=dict(zip(JOINTS, range(1000, 1006))),
                      loads=dict.fromkeys(JOINTS, 0), current_raw=dict.fromkeys(JOINTS, 0),
                      temperatures=dict.fromkeys(JOINTS, 30))
        read_times.append(time.perf_counter()-read_start)
        if recorder is not None and recorder.due(tick):
            begin = time.perf_counter()
            recorder.capture({name: dict(pixels=pixels[index], timestamp=tick)
                              for index, name in enumerate(CAMERAS)}, sample,
                             time_s=tick-started, monotonic=tick)
            captured.append(time.perf_counter()-begin)
        elapsed = tick-started
        target = 40 if elapsed < 1 else max(28., 40-12*(elapsed-1)) if elapsed < 2 else (
            40 if elapsed < 3 else max(28., 40-12*(elapsed-3)))
        if recorder is not None:
            recorder.goal(target, dict(step=steps+1, feedback=sample,
                model=[0]*5+[target], wire_normalized=[0]*5+[target],
                wire_raw=dict.fromkeys(JOINTS, 1000), lateral_correction=dict(applied_deg=.527)),
                time_s=elapsed, monotonic=tick)
        steps += 1
        work_times.append(time.perf_counter()-tick)
    loop_elapsed = time.perf_counter()-started
    main_cpu = time.process_time()-main_cpu_start
    record = None if recorder is None else recorder.close(reason='benchmark_end')
    return dict(mode=mode, steps=steps, loop_s=loop_elapsed, main_process_cpu_s=main_cpu,
                achieved_loop_hz=(len(gaps)/sum(gaps) if gaps else None),
                fake_read_s=distribution(read_times), loop_work_s=distribution(work_times),
                gap_s=distribution(gaps), scheduling_lateness_s=distribution(lateness),
                capture_call_s=distribution(captured), recording=record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=5)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    import numpy as np
    import PIL
    args.out.mkdir(parents=True, exist_ok=False)
    generator = np.random.default_rng(1234)
    pixels = [generator.integers(0, 256, (480, 640, 3), dtype=np.uint8) for _ in CAMERAS]
    results = []
    for mode in ('off', 'thread', 'process'):
        result = run_case(mode, args.out/mode, args.seconds, pixels)
        results.append(result)
        print(json.dumps(dict(mode=mode, loop_hz=result['achieved_loop_hz'],
                              loop_work_s=result['loop_work_s'], gap_s=result['gap_s'])), flush=True)
    report = dict(schema='so101.recorder_fake_read_benchmark.v1', host=platform.platform(),
        python=platform.python_version(), numpy=np.__version__, pillow=PIL.__version__, pid=os.getpid(),
        synthetic_camera_shape=[3,480,640,3], camera_fps=10, requested_loop_hz=60,
        seconds_per_case=args.seconds, serial_access=False, motor_writes=0,
        limitation='Windows synthetic RGB and CPU-only fake reads; not Jetson bus A/B evidence.',
        cases=results)
    (args.out/'benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(args.out/'benchmark.json'))


if __name__ == '__main__':
    main()
