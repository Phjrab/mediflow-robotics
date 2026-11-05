"""유휴 LeLab 미리보기로 세 카메라를 확인한다. 로봇과 serial은 사용하지 않는다."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from artifacts import save_json
from check_lights_via_lelab import CAMERAS, MAPPING, jpeg_frames, states

ROOT = Path(__file__).resolve().parent


def stable_jpegs(response):
    # 미리보기는 연결 즉시 출력하므로 초기 자동 보정 프레임을 계속 소비한다.
    # 잠들어서 HTTP 버퍼에 초기 프레임이 쌓이게 하지 않는다.
    started = time.monotonic()
    while time.monotonic() - started < 3.0:
        jpeg_frames(response)
    return jpeg_frames(response)


def allowed_owners(code, stdout, stderr, astra=False, group_reader=None):
    if not astra:
        return code == 1 and not stdout.strip() and not stderr.strip()
    ids = stdout.split()
    if code != 0 or not ids or not all(pid.isdigit() for pid in ids):
        return False
    if any(word in stderr.lower() for word in ('password', 'permission', 'cannot', 'no such', 'denied')):
        return False
    try:
        return all(any(line.endswith('/astra-v4l2-bridge.service') for line in group_reader(pid).splitlines()) for pid in ids)
    except (OSError, ValueError, TypeError):
        return False


def occupancy(path, astra=False):
    query = subprocess.run(['sudo', '-n', 'fuser', str(path)], capture_output=True, text=True,
                           env=dict(os.environ, LC_ALL='C'))
    return allowed_owners(query.returncode, query.stdout, query.stderr, astra,
                          lambda pid: (Path('/proc') / pid / 'cgroup').read_text())


def main():
    from PIL import Image, ImageDraw, ImageStat
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    result = dict(states_before=states(), cameras={}, serial_open=False, register_writes=0,
                  captured_at_unix=time.time(), preview_warmup_seconds=3.0,
                  source='LeLab_preview_JPEG_NOT_POLICY_INPUT')
    bridge = subprocess.run(['systemctl', 'is-active', 'astra-v4l2-bridge.service'], capture_output=True, text=True)
    if bridge.returncode != 0:
        raise RuntimeError('Astra 브리지 비활성; 서비스는 자동 변경하지 않음')
    for name, device in CAMERAS.items():
        astra = name == 'ceiling_vertical'
        path = Path(device)
        row = dict(policy_key=MAPPING[name], device=device, producer_occupancy_expected=astra)
        result['cameras'][name] = row
        if not path.exists():
            row['status'] = 'BLOCKED_MISSING_DEVICE'
            continue
        if not occupancy(path, astra):
            row['status'] = 'BLOCKED_UNEXPECTED_OWNER_OR_QUERY_ERROR'
            continue
        node = re.fullmatch(r'video(\d+)', path.resolve().name)
        if not node:
            row['status'] = 'BLOCKED_VIDEO_NODE_UNVERIFIED'
            continue
        states()
        try:
            url = 'http://127.0.0.1:8022/camera-preview/%s?width=640&height=480&fps=30' % node.group(1)
            with urllib.request.urlopen(url, timeout=5) as response:
                raw_frames = stable_jpegs(response)
            for index, raw in enumerate(raw_frames):
                image = Image.open(io.BytesIO(raw)).convert('RGB')
                if image.size != (640, 480):
                    raise ValueError('카메라 해상도 불일치')
                image.save(out / ('%s_%d.png' % (name, index)))
            row.update(status='CAPTURED', resolution=[640, 480], mean_rgb=ImageStat.Stat(image).mean,
                       frames=2, identical_jpeg=raw_frames[0] == raw_frames[1], astra_preview_overlay=astra)
        except Exception as error:
            row.update(status='ERROR', error=type(error).__name__, reason=str(error))
        finally:
            for _ in range(15):
                if occupancy(path, astra):
                    break
                time.sleep(.2)
            row['cleanup_expected'] = occupancy(path, astra)
        if not row['cleanup_expected']:
            break
    result['states_after'] = states()
    sheet = Image.new('RGB', (1280, 780), 'white')
    draw = ImageDraw.Draw(sheet)
    for r, camera in enumerate(CAMERAS):
        for c, color in enumerate(('red', 'green', 'blue', 'current')):
            path = ROOT / 'reports/offline_samples' / (color + '_' + camera + '_start.png') if c < 3 else out / (camera + '_1.png')
            draw.text((c * 320 + 5, r * 260 + 3), MAPPING[camera] + ' ' + color, fill='black')
            if path.is_file():
                sheet.paste(Image.open(path).convert('RGB').resize((320, 240)), (c * 320, r * 260 + 20))
            else:
                draw.text((c * 320 + 5, r * 260 + 100), 'NOT_RUN', fill='red')
    sheet.save(out / 'comparison_three_cameras.png')
    save_json(out / 'THREE_CAMERAS.json', result)
    print(json.dumps(dict(out=out.name, cameras=result['cameras'], serial_open=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
