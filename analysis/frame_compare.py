"""Compare camera frames and capture a camera-only diagnostic without opening the motor bus."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import time
from urllib.request import urlopen

CAMERAS = ('end_effector','ceiling_vertical','ceiling_oblique')


def compare(training, current, destination, *, current_label='Live first observation'):
    from PIL import Image, ImageDraw

    canvas = Image.new('RGB',(1280, 3*510),'white')
    draw = ImageDraw.Draw(canvas)
    for i, role in enumerate(CAMERAS):
        for column, (root, prefix, label) in enumerate(((training,'training','Blue training'),
                                                        (current,'live',current_label))):
            with Image.open(root/f'{prefix}_{role}.png') as frame:
                if frame.size != (640,480) or frame.mode != 'RGB':
                    raise ValueError(f'{role} source resolution or RGB mismatch')
                canvas.paste(frame, (640*column,510*i+26))
            draw.text((640*column+12,510*i+7),f'{label} | camera{i+1} | {role}',fill='black')
    canvas.save(destination)


def camera_only(out):
    from first_trial_dependencies import prepare
    prepare()
    import device_preflight
    import cv2
    from PIL import Image
    from observation_contract import load_contract

    preflight = device_preflight.inspect()
    if preflight['astra'] != 'active' or any(not preflight['devices'][name]['owners_ok'] for name in CAMERAS):
        raise RuntimeError(f'camera ownership check failed: {preflight}')
    if preflight['lelab'] == 'active':
        for action in ('teleoperation','recording','inference'):
            with urlopen(f'http://127.0.0.1:8022/{action}-status', timeout=3) as response:
                if json.load(response).get(f'{action}_active') is not False:
                    raise RuntimeError('LeLab is using the robot or cameras')
    contract = load_contract(prepare()/'resolved_camera_config.json')

    def capture(role):
        reader = cv2.VideoCapture(contract['cameras'][role], cv2.CAP_V4L2)
        try:
            if not reader.isOpened():
                raise RuntimeError(f'{role} camera could not open')
            if role=='ceiling_oblique':
                reader.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'YUYV'))
            for key, value in ((cv2.CAP_PROP_FRAME_WIDTH,640),(cv2.CAP_PROP_FRAME_HEIGHT,480),(cv2.CAP_PROP_FPS,30)):
                reader.set(key,value)
            failures, prev, gaps = 0, None, []
            stop = time.monotonic() + 4
            while time.monotonic() < stop:
                ok, frame = reader.read()
                now = time.monotonic()
                if not ok or frame is None:
                    failures += 1
                    time.sleep(.01)
                    continue
                if frame.shape != (480,640,3):
                    raise ValueError(f'{role} frame resolution mismatch {frame.shape}')
                if prev is not None:
                    gaps.append(now-prev)
                prev = now
                if len(gaps) >= 8 and stop-now < 0.9 and not (out/f'live_{role}.png').exists():
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    Image.fromarray(rgb).save(out/f'live_{role}.png')
            if not (out/f'live_{role}.png').is_file():
                raise RuntimeError(f'{role} did not provide nine frames')
            return {'role':role,'width':640,'height':480,'requested_fps':30,
                    'driver_fps':reader.get(cv2.CAP_PROP_FPS),'read_failures':failures,
                    'max_frame_gap_s':max(gaps,default=0),'rotation_deg':0}
        finally:
            reader.release()

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(capture,CAMERAS))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--training',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    args.out.mkdir(exist_ok=False, parents=True)
    details = camera_only(args.out)
    compare(args.training,args.out,args.out/'camera_comparison.png',current_label='Current camera-only capture')
    (args.out/'cameras.json').write_text(json.dumps(details,indent=2)+'\n')
    print(json.dumps(details),flush=True)
