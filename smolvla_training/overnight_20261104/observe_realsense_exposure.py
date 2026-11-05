"""카메라 제어값을 바꾸지 않고 미리보기 초기/안정화 프레임을 관측한다."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from artifacts import save_json
from check_lights_via_lelab import states
from check_three_cameras import occupancy

ROOT = Path(__file__).resolve().parent
DEVICE = '/dev/v4l/by-id/usb-Intel_R__RealSense_TM__Depth_Camera_435_Intel_R__RealSense_TM__Depth_Camera_435-video-index0'


def frames(stream, max_bytes=32_000_000):
    buffer = b''
    total = 0
    while True:
        chunk = stream.read(4096)
        if not chunk:
            raise ValueError('미리보기 스트림 조기 종료')
        total += len(chunk)
        if total > max_bytes:
            raise ValueError('수신 상한 초과')
        buffer += chunk
        while True:
            start = buffer.find(b'\xff\xd8')
            end = buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
            if start < 0 or end < 0:
                break
            yield buffer[start:end + 2]
            buffer = buffer[end + 2:]


def image_stats(raw):
    import numpy as np
    from PIL import Image
    image = Image.open(io.BytesIO(raw)).convert('RGB')
    if image.size != (640, 480):
        raise ValueError('영상 크기 불일치')
    array = np.asarray(image, dtype=np.float64)
    gray = array.mean(axis=2)
    return image, dict(mean_rgb=array.mean(axis=(0, 1)).tolist(), brightness=float(gray.mean()),
                       quantiles=np.percentile(gray, [1, 5, 50, 95, 99]).tolist(),
                       fraction_gray_above240=float((gray >= 240).mean()),
                       fraction_gray_below20=float((gray < 20).mean()),
                       tiles_mean=[[float(tile.mean()) for tile in row] for row in np.stack(np.split(np.stack(np.split(gray, 4, axis=0)), 4, axis=2), axis=1)])


def read_controls(reader):
    result = subprocess.run([str(reader), DEVICE], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    reader = ROOT / 'reports/realsense_exposure_20261105/read_controls'
    path = Path(DEVICE)
    states()
    if not occupancy(path):
        raise RuntimeError('카메라 점유 또는 조회 오류')
    before = read_controls(reader)
    node = path.resolve().name.removeprefix('video')
    if not node.isdecimal():
        raise ValueError('영상 노드 미확인')
    samples = []
    targets = [0, .5, 1, 2, 4, 6]
    controls = {}
    started = time.monotonic()
    try:
        url = 'http://127.0.0.1:8022/camera-preview/%s?width=640&height=480&fps=30' % node
        with urllib.request.urlopen(url, timeout=5) as stream:
            for count, raw in enumerate(frames(stream)):
                elapsed = time.monotonic() - started
                if elapsed > 12:
                    raise TimeoutError('관측 시간 초과')
                if len(samples) < len(targets) and elapsed >= targets[len(samples)]:
                    image, stats = image_stats(raw)
                    name = 'frame_%02d.png' % len(samples)
                    image.save(args.out / name)
                    samples.append(dict(file=name, received_seconds=elapsed, received_frame_index=count, **stats))
                    controls[name] = read_controls(reader)
                if len(samples) == len(targets):
                    break
    finally:
        for _ in range(15):
            if occupancy(path):
                break
            time.sleep(.2)
    after = read_controls(reader)
    result = dict(before=before, during=controls, after=after, samples=samples,
                  camera_free_after=occupancy(path), states_after=states(),
                  setting_writes=0, serial_access='NOT_RUN', frame_time_source='HTTP_RECEIVE_NOT_SENSOR_TIMESTAMP')
    save_json(args.out / 'OBSERVATION.json', result)
    print(json.dumps(dict(samples=[dict(seconds=round(x['received_seconds'], 3), brightness=round(x['brightness'], 3), mean_rgb=x['mean_rgb']) for x in samples], camera_free_after=result['camera_free_after'], setting_writes=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
