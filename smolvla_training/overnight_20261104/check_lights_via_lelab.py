"""LeLab이 유휴 상태일 때 미리보기만 받는다. serial·모터·정책은 사용하지 않는다."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from observation_contract import load_contract,display_mapping,warm_preview

ROOT = Path(__file__).resolve().parent
CAMERAS = load_contract()['cameras']


def states():
    result = {}
    for name, key in [('recording', 'recording_active'), ('teleoperation', 'teleoperation_active'), ('inference', 'inference_active')]:
        with urllib.request.urlopen('http://127.0.0.1:8022/' + name + '-status', timeout=5) as response:
            data = json.load(response)
        if data.get(key) is not False:
            raise RuntimeError('중단: LeLab 작업 중 또는 상태 미확인: ' + name)
        result[name] = False
    return result


def free(path):
    result = subprocess.run(['sudo', '-n', 'fuser', str(path)], capture_output=True, text=True)
    return result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip()


def jpeg_frames(response, count=2, max_bytes=4_000_000):
    data = b''
    frames = []
    total = 0
    deadline = time.monotonic() + 12
    while len(frames) < count:
        if time.monotonic() > deadline:
            raise TimeoutError('미리보기 프레임 수신 시간 초과')
        chunk = response.read(4096)
        if not chunk:
            raise ValueError('미리보기 스트림 조기 종료')
        total += len(chunk)
        if total > max_bytes:
            raise ValueError('미리보기 수신 용량 상한')
        data += chunk
        while len(frames) < count:
            start = data.find(b'\xff\xd8')
            end = data.find(b'\xff\xd9', start + 2) if start >= 0 else -1
            if start < 0 or end < 0:
                break
            frames.append(data[start:end + 2])
            data = data[end + 2:]
    return frames


def main():
    from PIL import Image, ImageDraw, ImageStat
    contract=load_contract();mapping=display_mapping()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    result = {'states_before': states(), 'serial_open': False, 'register_writes': 0,
              'source': 'LeLab_preview_JPEG_NOT_POLICY_INPUT', 'cameras': {}}
    for name, device in CAMERAS.items():
        row = {'policy_key': mapping[name], 'device': device}
        result['cameras'][name] = row
        if name == 'ceiling_vertical':
            status = subprocess.run(['systemctl', 'is-active', 'astra-v4l2-bridge.service'], capture_output=True, text=True)
            if status.returncode != 0:
                row['status'] = 'NOT_RUN_ASTRA_BRIDGE_INACTIVE'
                continue
            # producer가 실행 중인 Astra는 이 도구에서 새 미리보기를 열지 않는다.
            row['status'] = 'NOT_RUN_PRODUCER_REVIEW_SEPARATE'
            continue
        path = Path(device)
        if not path.exists() or not free(path):
            row['status'] = 'BLOCKED_MISSING_BUSY_OR_QUERY_ERROR'
            continue
        match = re.fullmatch(r'video(\d+)', path.resolve().name)
        if not match:
            row['status'] = 'BLOCKED_VIDEO_NODE_UNVERIFIED'
            continue
        states()
        try:
            url = 'http://127.0.0.1:8022/camera-preview/%s?width=640&height=480&fps=30' % match.group(1)
            with urllib.request.urlopen(url, timeout=5) as response:
                frames = warm_preview(response,jpeg_frames,contract['warmup_seconds'])
            for index, raw in enumerate(frames):
                image = Image.open(io.BytesIO(raw)).convert('RGB')
                if image.size != (640, 480):
                    raise ValueError('미리보기 해상도 불일치')
                image.save(out / ('%s_%d.png' % (name, index)))
            row.update(status='CAPTURED', resolution=[640, 480], mean_rgb=ImageStat.Stat(image).mean,
                       frames=2, identical_jpeg=frames[0] == frames[1])
        except Exception as error:
            row.update(status='ERROR', error=type(error).__name__, reason=str(error))
        finally:
            for _ in range(10):
                if free(path):
                    break
                time.sleep(.2)
            row['port_free_after'] = free(path)
        if not row['port_free_after']:
            row['cleanup'] = 'UNVERIFIED_SERVER_HANDLE_REMAINS_NO_SERVICE_CHANGE'
            break
    result['states_after'] = states()
    sheet = Image.new('RGB', (1280, 780), 'white')
    draw = ImageDraw.Draw(sheet)
    for r, camera in enumerate(CAMERAS):
        for c, color in enumerate(('red', 'green', 'blue', 'lights_on')):
            path = (ROOT / 'reports/offline_samples' / (color + '_' + camera + '_start.png')) if c < 3 else out / (camera + '_1.png')
            draw.text((c * 320 + 5, r * 260 + 3), mapping[camera] + ' ' + color, fill='black')
            if path.is_file():
                sheet.paste(Image.open(path).convert('RGB').resize((320, 240)), (c * 320, r * 260 + 20))
            else:
                draw.text((c * 320 + 5, r * 260 + 100), 'NOT_RUN', fill='red')
    sheet.save(out / 'comparison_lights.png')
    with (out / 'LIGHTS_CHECK.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'out': out.name, 'cameras': result['cameras'], 'serial_open': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
