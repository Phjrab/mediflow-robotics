"""Compare the live reader with JPEG ring recording off/on; deny register writes.

Uses identical, saved RGB camera frames in both arms of the comparison. This
isolates recorder overhead, not camera acquisition or model inference. Torque
must already be OFF; this tool never changes it or stops an owning process.
"""
import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from communication_retry import CommunicationPolicy, RetryableWireError
from config_runtime import load
from direct_live import read_sync_replies
from grasp_recorder import CAMERAS, GraspRecorder
from loop_timing import LoopTiming, terminal_lines
from individual_reply_guard import StrictIndividualReplies
from servo_thermal_readonly import require_unowned_port
from thermal_read import read_temperatures


def allow_read(packet, ids):
    raw = bytes(packet)
    header = (len(raw) >= 8 and raw[:2] == b'\xff\xff'
              and raw[3] == len(raw)-4 and sum(raw[2:]) % 256 == 255)
    if not header:
        raise RuntimeError('유효하지 않은 READ 패킷')
    location = tuple(raw[5:7]) in ((33, 8), (56, 15), (63, 1))
    individual = len(raw) == 8 and raw[4] == 2 and raw[2] in ids
    group = raw[4] == 0x82 and raw[2] == 254 and list(raw[7:-1]) == list(ids)
    if not (header and location and (individual or group)):
        raise RuntimeError('READ 이외 패킷 거부; 레지스터 쓰기 금지')
    return raw


def distribution(values):
    ordered = sorted(values)
    def percentile(p):
        return ordered[min(len(ordered)-1, round((len(ordered)-1)*p))] if ordered else None
    return dict(n=len(ordered), mean=statistics.mean(ordered) if ordered else None,
                p50=percentile(.5), p95=percentile(.95), p99=percentile(.99),
                max=max(ordered, default=None))


def verify_off(mode, data, temperatures=None):
    joints = load()['joint_names']
    if (set(mode) != set(joints) or set(data) != set(joints)
            or any(len(mode[j]) != 8 or len(data[j]) != 15 for j in joints)):
        raise RuntimeError('읽기 비교의 6축 응답/길이 누락')
    bad = {j: dict(mode=mode[j][0], torque=mode[j][7],
                   status=data[j][9], temperature_c=data[j][7])
           for j in mode if mode[j][0] != 0 or mode[j][7] != 0
           or data[j][9] != 0 or data[j][7] >= 55}
    if bad:
        raise RuntimeError('읽기 비교 조건 불충족(토크 OFF/상태/온도): '+json.dumps(bad))
    if temperatures is not None:
        if set(temperatures) != set(joints):
            raise RuntimeError('6축 개별 온도 누락')
        hot = {j: value for j, value in temperatures.items() if value >= 55}
        if hot:
            raise RuntimeError('읽기 비교 개별 온도 55°C 이상: '+json.dumps(hot))


def close_bus_readonly(bus):
    """Close the transport directly; never invoke robot/bus torque cleanup."""
    errors = []
    try:
        bus.port_handler.closePort()
    except Exception as error:
        errors.append(dict(operation='closePort', reason=str(error)))
        try:
            bus.port_handler.ser.close()
        except Exception as fallback:
            errors.append(dict(operation='serial.close', reason=str(fallback)))
    port = bus.port_handler
    serial = getattr(port, 'ser', None)
    closed = not bool(getattr(serial, 'is_open', getattr(port, 'is_open', True)))
    return closed, errors


def run_phase(bus, config, order, images, output_path, duration, enabled, *,
              recorder_factory=GraspRecorder, clock=time.monotonic, pause=time.sleep):
    name = 'ring_on' if enabled else 'ring_off'
    joints = config['joint_names'];ids = {j: i+1 for i, j in enumerate(joints)}
    phase = dict(name=name, completed=False, cleanup_errors=[], mode_cache_max_age_s=.05,
                 individual_temperature_interval_s=.02,
                 thermal_stop_rule='diagnostic_raw_or_individual_55C_no_thermal_window')
    recorder = policy = timing = None
    work, gaps, read_times, enqueue_times = [], [], [], []
    start, cpu_start = clock(), time.process_time()
    try:
        with (output_path/(name+'.jsonl')).open('x', encoding='utf-8') as output:
            def log(row):
                output.write(json.dumps(row, separators=(',', ':'))+'\n')
            policy = CommunicationPolicy(joints, config['communication'], log, clock=clock, pause=pause)
            try:
                policy.attach(bus.port_handler)
                def block(address, width):
                    def once():
                        code = bus.packet_handler.syncReadTx(bus.port_handler, address, width,
                                                             list(order.values()), len(order))
                        if code:
                            raise RetryableWireError('SYNC_READ 송신 실패', {'communication': code})
                        return read_sync_replies(bus.packet_handler, bus.port_handler, order,
                                                 address, width, policy)
                    return policy.perform(list(ids.values()), f'read:{address}:{width}', once,
                                          port=bus.port_handler, packet_timeout_s=None)
                # Confirm OFF before starting any recorder. This tool must not
                # send the Torque_Enable write that would make it so.
                mode, data = block(33, 8), block(56, 15)
                verify_off(mode, data)
                if enabled: recorder = recorder_factory(output_path/name, joints)
                start, cpu_start = clock(), time.process_time()
                previous = None;mode_at = None;last_requests = {}
                timing = LoopTiming(start, config['speed'], log, clock=clock, policy_timeline=False)
                while clock()-start < duration:
                    tick = clock()
                    if previous is not None: gaps.append(tick-previous)
                    previous = tick
                    tick_record = timing.begin(tick)
                    tick_failed = True
                    try:
                        if mode_at is None or tick-mode_at > .05:
                            mode = tick_record.call('serial_read', block, 33, 8);mode_at = clock()
                        data = tick_record.call('serial_read', block, 56, 15)
                        verify_off(mode, data)
                        bulk = {j: row[7] for j, row in data.items()}
                        traces = []
                        temperatures, _ = tick_record.call('serial_read', read_temperatures, bus, ids, bulk, StrictIndividualReplies,
                            traces=traces, last_requests=last_requests, minimum_interval_s=.02,
                            clock=clock, pause=pause, retry=policy)
                        verify_off(mode, data, temperatures)
                        read_times.append(clock()-tick)
                        policy.activate()  # Carry the per-axis 200 ms watchdog across cycles.
                        sample = dict(present_raw={j: int.from_bytes(row[:2], 'little') for j, row in data.items()},
                            temperatures=temperatures, temperatures_bulk=bulk,
                            voltage_v={j: row[6]/10 for j, row in data.items()})
                        enqueue_started = clock()
                        if recorder:
                            now = clock()
                            tick_record.call('recorder_enqueue', recorder.capture, {j: dict(pixels=pixels, timestamp=now) for j, pixels in images.items()},
                                             sample, time_s=now-start, monotonic=now)
                        enqueue_times.append(clock()-enqueue_started)
                        work.append(clock()-tick)
                        log(dict(event='readonly_cycle', time_s=tick-start, read_and_enqueue_s=work[-1],
                                 individual_traces=traces, **sample))
                        tick_failed = False
                    finally:
                        tick_record.finish(failed=tick_failed)
                    pause(max(0, 1/60-(clock()-tick)))
                phase['completed'] = True
            finally:
                elapsed = clock()-start
                if timing is not None:
                    phase['loop_timing'] = timing.summary(clock())
                phase.update(elapsed_s=elapsed, work_s=distribution(work), cycle_s=distribution(gaps),
                             read_s=distribution(read_times), recorder_enqueue_s=distribution(enqueue_times),
                             main_process_cpu_pct=100*(time.process_time()-cpu_start)/max(elapsed, 1e-9))
                try:
                    phase['communication'] = policy.summary()
                    policy.detach()
                except Exception as error:
                    phase['cleanup_errors'].append(dict(operation='policy.detach', reason=str(error)))
    except (Exception, KeyboardInterrupt) as error:
        phase['error'] = dict(kind=type(error).__name__, reason=str(error))
        phase['completed'] = False
    finally:
        if recorder is not None:
            try:
                phase['recorder'] = recorder.close(reason='readonly comparison complete', timeout=10)
                if phase['recorder'].get('writer_alive') or phase['recorder'].get('errors'):
                    phase['cleanup_errors'].append(dict(operation='recorder.close', reason='writer alive or evidence errors'))
            except Exception as error:
                phase['cleanup_errors'].append(dict(operation='recorder.close', reason=str(error)))
        if phase['cleanup_errors']:phase['completed'] = False
    return phase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--frames', type=Path, required=True,
                        help='end_effector.jpg, ceiling_vertical.jpg, ceiling_oblique.jpg')
    parser.add_argument('--duration', type=float, default=60)
    args = parser.parse_args()
    if not 1 <= args.duration <= 60:
        parser.error('각 조건의 읽기 시간은 1~60초')
    config = load()
    cal_path = Path(config['calibration']).expanduser()
    if hashlib.sha256(cal_path.read_bytes()).hexdigest() != config['calibration_sha256']:
        raise RuntimeError('보정 해시 불일치; 포트를 열지 않음')
    device = Path(config['port']).resolve(strict=True)
    require_unowned_port(device)
    from PIL import Image
    import numpy as np
    images = {name: np.asarray(Image.open(args.frames/(name+'.jpg')).convert('RGB')).copy()
              for name in CAMERAS}
    if any(frame.shape != (480, 640, 3) or frame.dtype != np.uint8 for frame in images.values()):
        raise RuntimeError('비교 영상은 학습과 같은 640×480 RGB uint8 3장이어야 함; 포트를 열지 않음')
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    joints = config['joint_names']
    ids = {j: i+1 for i, j in enumerate(joints)}
    order = {j: mid for mid in config['communication']['read_order'] for j in joints if ids[j] == mid}
    calibration = json.loads(cal_path.read_text())
    bus = FeetechMotorsBus(port=config['port'],
        motors={j: Motor(ids[j], 'sts3215', MotorNormMode.DEGREES if i < 5 else MotorNormMode.RANGE_0_100)
                for i, j in enumerate(joints)},
        calibration={j: MotorCalibration(**calibration[j]) for j in joints})
    original_write = bus.port_handler.writePort
    def read_only(packet):
        return original_write(allow_read(packet, list(order.values())))
    bus.port_handler.writePort = read_only
    args.out.mkdir(parents=True, exist_ok=False)
    result = dict(status='HOLD', live='NOT_RUN', motor_register_writes=0,
                  frame_source='saved_identical_RGB_no_camera_acquisition', phases=[], cleanup_errors=[])
    try:
        bus.connect(handshake=False)
        import fcntl
        import termios
        fcntl.flock(bus.port_handler.ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(bus.port_handler.ser.fileno(), termios.TIOCEXCL)
        for enabled in (False, True):
            phase = run_phase(bus, config, order, images, args.out, args.duration, enabled)
            result['phases'].append(phase)
            if not phase['completed']:
                raise RuntimeError('읽기 비교 중단: '+json.dumps(phase.get('error',phase['cleanup_errors'])))
        result['status'] = 'RECORDER_BUS_READONLY_COMPLETE'
    except (Exception, KeyboardInterrupt) as error:
        result['error'] = dict(kind=type(error).__name__, reason=str(error))
    finally:
        result['port_closed'], errors = close_bus_readonly(bus)
        result['cleanup_errors'].extend(errors)
        if not result['port_closed'] or errors:result['status'] = 'HOLD'
        (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for phase in result['phases']:
        print('읽기 비교 '+phase['name'])
        for line in terminal_lines(phase.get('loop_timing')): print(line)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'RECORDER_BUS_READONLY_COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
