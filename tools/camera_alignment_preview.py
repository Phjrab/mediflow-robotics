"""Camera-only preview and alignment. Never instantiate/connect a robot."""
import base64
import hashlib
import io
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from alignment_metrics import compare_markers, detect_markers, deny_serial

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'tools/monitor_web/alignment'
DIRECTORY = Path.home()/'.cache/so101-vla-monitor'


def save(path, value):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def wait_for_runner(stop):
    import fcntl
    path = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))/'so101-vla-live.lock'
    with path.open('a') as lock:
        while not stop.is_set():
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
                return
            except BlockingIOError:
                stop.wait(.5)
    raise InterruptedError('정렬 미리보기 종료')


def main():
    sys.addaudithook(deny_serial)
    from PIL import Image
    import numpy as np
    from config_runtime import load, contract
    from stream_live import device_class
    c = load()
    metadata = json.loads((ASSETS/'reference.json').read_text())
    references = {}
    for role, row in metadata['cameras'].items():
        path = ASSETS/row['filename']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('학습 첫 프레임 해시 불일치: '+role)
        pixels = np.asarray(Image.open(path).convert('RGB'))
        references[role] = (pixels, detect_markers(pixels))
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    state = dict(schema=1, phase='opening', captured=time.time(), motor_access=False,
                 register_writes=0, images={}, cameras={})
    save(DIRECTORY/'alignment.json', state)
    device = None
    def check():
        if stop.is_set():
            raise InterruptedError('정렬 미리보기 종료')
    try:
        wait_for_runner(stop)
        device = device_class()({'contract': contract(c), 'home': c['home']}, lambda _: None, DIRECTORY)
        device.open_cameras(check)
        while not stop.is_set():
            frames = device.camera_frames()
            stamp = time.time()
            state = dict(schema=1, phase='ready' if frames is not None else 'no_fresh_frame',
                         captured=stamp, motor_access=False, register_writes=0, images={}, cameras={})
            if frames is not None:
                for role, row in frames.items():
                    pixels = row['pixels']
                    reference, markers = references[role]
                    metric = compare_markers(markers, detect_markers(pixels))
                    metric.update(brightness=float(pixels.mean()), reference_brightness=float(reference.mean()),
                                  rgb_mean=pixels.mean((0, 1)).tolist(), reference_rgb_mean=reference.mean((0, 1)).tolist())
                    output = io.BytesIO()
                    Image.fromarray(pixels).save(output, 'JPEG', quality=88)
                    state['images'][role] = base64.b64encode(output.getvalue()).decode('ascii')
                    state['cameras'][role] = metric
            state['camera_stats'] = device.cameras_stats.summary()
            save(DIRECTORY/'alignment.json', state)
            stop.wait(.3)
    except InterruptedError:
        pass
    except Exception as error:
        state.update(phase='error', error={'kind': type(error).__name__, 'reason': str(error)}, captured=time.time())
        save(DIRECTORY/'alignment.json', state)
        raise
    finally:
        if device is not None:
            device.close()
        state.update(phase='closed', closed_at=time.time())
        save(DIRECTORY/'alignment.json', state)


if __name__ == '__main__':
    main()
