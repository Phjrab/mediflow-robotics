"""Read-only audit of three pinned Hub datasets; all outputs in fresh folders."""
import bisect
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).parent / 'recording-inspect-deps'))
import av
import pyarrow.parquet as pq

SOURCES = {
    'red': ('Supermassive111/a_to_red_20261102_201029', 'c14477c22cb0cf3b27599a32b96222631380720e'),
    'green': ('Supermassive111/a_to_green_20261102_201804', 'ecc9d395cdce6bcc8a44ffbd6840ebe2f2d2466d'),
    'blue': ('Supermassive111/a_to_blue_20261102_203209', '91da17d4ad12e361874acfcd12c2f6f4a68ffbff'),
}
FAILED_ORDINALS = {'red': [2], 'green': [], 'blue': [2, 6]}
DROP_ORDINALS = {'red': [], 'green': [5, 6], 'blue': [9]}
STAMP = time.strftime('%Y%m%d_%H%M%S')
RAW = Path('.local') / ('three_color_checks_' + STAMP)
OUT = Path('reports') / ('a_color_audit_' + STAMP)
RAW.mkdir(parents=True, exist_ok=False)
OUT.mkdir(parents=True, exist_ok=False)


def save(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def get_json(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.load(response)


def download(job):
    color, repo, revision, name = job
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError('invalid Hub file path')
    dest = RAW / color / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = 'https://huggingface.co/datasets/' + repo + '/resolve/' + revision + '/' + name
    with urllib.request.urlopen(url, timeout=120) as response, dest.open('xb') as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    return dest.stat().st_size


def read_parquets(paths):
    rows = []
    for path in sorted(paths):
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def audit(color):
    repo, revision = SOURCES[color]
    root = RAW / color
    info = json.loads((root / 'meta/info.json').read_text(encoding='utf-8'))
    fps = info['fps']
    episodes = sorted(read_parquets((root / 'meta/episodes').rglob('*.parquet')), key=lambda e: e['episode_index'])
    rows = read_parquets((root / 'data').rglob('*.parquet'))
    tasks = {t['task_index']: t['task'] for t in pq.read_table(root / 'meta/tasks.parquet').to_pylist()}
    expected = f'Put medicine bottle A into the {color} basket'
    grouped = {}
    for row in rows:
        grouped.setdefault(row['episode_index'], []).append(row)
    lengths = [len(grouped.get(e['episode_index'], [])) for e in episodes]
    median = statistics.median(lengths)
    summary = {
        'repo_id': repo, 'revision': revision, 'format': info.get('codebase_version'), 'fps': fps,
        'declared_episodes': info.get('total_episodes'), 'episodes': len(episodes),
        'declared_frames': info.get('total_frames'), 'frames': len(rows),
        'expected_task': expected, 'task_table': tasks, 'median_episode_frames': median,
        'short_rule': 'frames < 2*fps OR frames < 0.5*median; candidate only, never auto-excluded',
        'repeat_rule': 'exact decoded RGB equality; longest run >=1 second flagged; stationary scenes possible',
        'features': {k: v for k, v in info['features'].items() if k in ('action', 'observation.state')},
        'episode_checks': [], 'cameras': [], 'issues': [],
    }
    if len(rows) != info['total_frames'] or len(episodes) != info['total_episodes']:
        summary['issues'].append('DECLARED_COUNTS_MISMATCH')
    by_id = {}
    for ep, length in zip(episodes, lengths):
        eid = ep['episode_index']
        samples = sorted(grouped.get(eid, []), key=lambda r: r['frame_index'])
        ts = [r['timestamp'] for r in samples]
        row_tasks = sorted(set(tasks.get(r['task_index'], '<MISSING>') for r in samples))
        state_dims = sorted(set(len(r['observation.state']) for r in samples))
        action_dims = sorted(set(len(r['action']) for r in samples))
        issues = []
        if length != ep['length']: issues.append('EPISODE_LENGTH_MISMATCH')
        if state_dims != [6] or action_dims != [6]: issues.append('DIMENSION_MISMATCH')
        if row_tasks != [expected] or ep.get('tasks') != [expected]: issues.append('TASK_MISMATCH')
        if ep['dataset_to_index'] - ep['dataset_from_index'] != length: issues.append('DATA_SLICE_MISMATCH')
        short = length < 2 * fps or length < 0.5 * median
        record = {
            'episode_index': eid, 'user_episode_number': eid + 1,
            'frames': length, 'seconds': length / fps, 'declared_frames': ep['length'],
            'state_dims': state_dims, 'action_dims': action_dims, 'tasks': row_tasks,
            'timestamp_bad_intervals': sum(abs(b-a-1/fps) > .001 for a,b in zip(ts, ts[1:])),
            'nonfinite_values': sum(not math.isfinite(float(v)) for r in samples for k in ('observation.state','action') for v in r[k]),
            'short_candidate': short, 'issues': issues, 'video': {},
        }
        summary['episode_checks'].append(record)
        by_id[eid] = record
    for key, feature in info['features'].items():
        if feature.get('dtype') != 'video': continue
        files = {}
        for ep in episodes:
            chunk = ep[f'videos/{key}/chunk_index']
            file_index = ep[f'videos/{key}/file_index']
            rel = info['video_path'].format(video_key=key, camera_key=key, chunk_index=chunk, file_index=file_index)
            files.setdefault(rel, []).append(ep)
            by_id[ep['episode_index']]['video'][key] = {'frames': 0, 'uniform_frames': 0, 'black_frames': 0, 'adjacent_identical_pairs': 0, 'max_identical_run': 0}
        camera = {'key': key, 'declared_shape': feature.get('shape'), 'files': [], 'issues': []}
        for relative, file_episodes in files.items():
            checks = {'file': relative, 'decoded_frames': 0, 'unmapped_frames': 0}
            windows = sorted((e[f'videos/{key}/from_timestamp'], e[f'videos/{key}/to_timestamp'], e['episode_index']) for e in file_episodes)
            starts = [w[0] for w in windows]
            previous = {}; runs = {}
            try:
                with av.open(str(root / relative)) as container:
                    stream = container.streams.video[0]
                    checks.update({'resolution': [stream.width, stream.height], 'fps': str(stream.average_rate), 'codec': stream.codec_context.name})
                    if [stream.height, stream.width, 3] != feature.get('shape'): camera['issues'].append('RESOLUTION_MISMATCH')
                    if abs(float(stream.average_rate) - fps) > .001: camera['issues'].append('FPS_MISMATCH')
                    for frame in container.decode(stream):
                        checks['decoded_frames'] += 1
                        if frame.pts is None:
                            checks['unmapped_frames'] += 1; continue
                        timestamp = float(frame.pts * frame.time_base)
                        pos = bisect.bisect_right(starts, timestamp + 1e-6) - 1
                        if pos < 0 or timestamp >= windows[pos][1] - 1e-6:
                            checks['unmapped_frames'] += 1; continue
                        eid = windows[pos][2]
                        record = by_id[eid]['video'][key]
                        image = frame.to_image()
                        extrema = image.getextrema()
                        uniform = all(high-low <= 2 for low,high in extrema)
                        black = max(high for low,high in extrema) <= 3
                        digest = hashlib.blake2b(image.tobytes(), digest_size=16).digest()
                        record['frames'] += 1
                        record['uniform_frames'] += int(uniform)
                        record['black_frames'] += int(black)
                        if previous.get(eid) == digest:
                            record['adjacent_identical_pairs'] += 1
                            runs[eid] += 1
                        else: runs[eid] = 1
                        previous[eid] = digest
                        record['max_identical_run'] = max(record['max_identical_run'], runs[eid])
            except Exception as error:
                checks['decode_error'] = type(error).__name__
                camera['issues'].append('VIDEO_DECODE_ERROR')
            camera['files'].append(checks)
        for ep_record in summary['episode_checks']:
            v = ep_record['video'][key]
            v['max_identical_seconds'] = v['max_identical_run'] / fps
            v['long_repeat_candidate'] = v['max_identical_run'] >= fps
            if v['frames'] != ep_record['frames']:
                ep_record['issues'].append('VIDEO_FRAME_COUNT_MISMATCH:' + key)
            if v['frames'] == 0: ep_record['issues'].append('EMPTY_VIDEO:' + key)
            if v['uniform_frames']: ep_record['issues'].append('UNIFORM_FRAME_CANDIDATE:' + key)
        summary['cameras'].append(camera)
    summary['state_dims'] = sorted(set(d for e in summary['episode_checks'] for d in e['state_dims']))
    summary['action_dims'] = sorted(set(d for e in summary['episode_checks'] for d in e['action_dims']))
    summary['all_tasks_exact'] = all(e['tasks'] == [expected] and 'TASK_MISMATCH' not in e['issues'] for e in summary['episode_checks'])
    save(OUT / ('audit_' + color + '.json'), summary)
    return summary


jobs = []
for color, (repo, revision) in SOURCES.items():
    metadata = get_json('https://huggingface.co/api/datasets/' + repo + '/revision/' + revision)
    for entry in metadata['siblings']:
        if entry['rfilename'].endswith(('.json', '.parquet', '.mp4')):
            jobs.append((color, repo, revision, entry['rfilename']))
print(json.dumps({'phase': 'download', 'files': len(jobs), 'reports': str(OUT), 'raw': str(RAW)}), flush=True)
with ThreadPoolExecutor(max_workers=4) as executor:
    total_bytes = sum(executor.map(download, jobs))
print(json.dumps({'phase': 'download_complete', 'bytes': total_bytes}), flush=True)
results = {}
for color in SOURCES:
    results[color] = audit(color)
    d = results[color]
    print(json.dumps({'phase': 'audited', 'color': color, 'episodes': d['episodes'], 'frames': d['frames'], 'task_exact': d['all_tasks_exact']}), flush=True)
exclusions = {'schema_version': 1, 'numbering': {'basis': 'one_based_ordinal', 'user_clarification': '번째 에피소드', 'conversion': 'episode_index = ordinal - 1'}, 'automatic_exclusions': False, 'datasets': {}}
for color, data in results.items():
    eps = {e['episode_index']: e for e in data['episode_checks']}
    excluded = []
    notes = []
    for ordinal in FAILED_ORDINALS[color]:
        eid = ordinal - 1
        e = eps[eid]
        excluded.append({'user_episode_number': ordinal, 'episode_index': eid, 'reason': 'USER_REPORTED_FAILURE', 'frames': e['frames'], 'seconds': e['seconds']})
    for ordinal in DROP_ORDINALS[color]:
        eid = ordinal - 1
        e = eps[eid]
        notes.append({'user_episode_number': ordinal, 'episode_index': eid, 'note': 'USER_REPORTED_DROP_KEEP_INCLUDED', 'excluded': False, 'frames': e['frames'], 'seconds': e['seconds']})
    excluded_ids = [e['episode_index'] for e in excluded]
    included = sorted(set(eps) - set(excluded_ids))
    exclusions['datasets'][color] = {'repo_id': data['repo_id'], 'revision': data['revision'], 'excluded_episodes': excluded_ids, 'excluded_details': excluded, 'included_episodes': included, 'reference_notes': notes, 'source_frames': data['frames'], 'remaining_frames': sum(eps[eid]['frames'] for eid in included), 'audit': 'audit_' + color + '.json'}
save(OUT / 'excluded_episodes.json', exclusions)
save(OUT / 'summary.json', {'datasets': results, 'exclusions': exclusions, 'downloaded_bytes': total_bytes})
print(json.dumps({'phase': 'complete', 'reports': str(OUT), 'excluded': {c:d['excluded_episodes'] for c,d in exclusions['datasets'].items()}}), flush=True)
