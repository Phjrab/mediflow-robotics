"""Recover all 137 training episodes and derive the elbow command upper tick."""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path


TRIMS = {('a_to_red_20261102_210433', 39): 455, ('a_to_blue_20261102_212958', 0): 516}


def analyze(datasets, mapping, calibration):
    import pyarrow.parquet as pq
    selections = list(csv.DictReader(mapping.open(encoding='utf-8')))
    if [int(x['merged_episode_index']) for x in selections] != list(range(137)):
        raise ValueError('137회 학습 매핑 불일치')
    by_source = collections.defaultdict(dict)
    for x in selections:
        by_source[x['source_repo_id'].split('/')[-1]][int(x['source_episode_index'])] = x
    limits = json.loads(calibration.read_text())['elbow_flex']
    center = (limits['range_min']+limits['range_max'])/2
    counts = collections.Counter()
    hashes, maxima = {}, {key: None for key in ('action', 'observation.state')}
    frames = 0
    for source, selection in by_source.items():
        root = datasets/source
        info = json.loads((root/'meta/info.json').read_text())
        for key in maxima:
            if info['features'][key]['names'][2] != 'elbow_flex.pos':
                raise ValueError('팔꿈치 열 순서 불일치')
        found = collections.defaultdict(list)
        for file in sorted((root/'data').rglob('*.parquet')):
            hashes[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
            for batch in pq.ParquetFile(file).iter_batches(columns=['episode_index','frame_index','action','observation.state']):
                for row in batch.to_pylist():
                    if row['episode_index'] in selection:
                        found[row['episode_index']].append(row)
        if set(found) != set(selection):
            raise ValueError('선택 회차 누락')
        for episode, meta in selection.items():
            rows = sorted(found[episode], key=lambda x:x['frame_index'])
            if (source, episode) in TRIMS:
                length = TRIMS[source, episode]
                if len(rows) not in (length, length+1):
                    raise ValueError('학습 마지막 행 조정 기록 불일치')
                rows = rows[:length]
            if [x['frame_index'] for x in rows] != list(range(len(rows))):
                raise ValueError('프레임 순서 불일치')
            counts[meta['color']] += 1
            frames += len(rows)
            for row in rows:
                for key in maxima:
                    value = float(row[key][2])
                    if not math.isfinite(value):
                        raise ValueError('비정상 팔꿈치 수치')
                    if maxima[key] is None or value > maxima[key]['degree']:
                        coordinate = value*4095/360+center
                        maxima[key] = dict(degree=value, follower_tick_coordinate=coordinate,
                            follower_sdk_integer_tick=int(coordinate), nearest_tick=round(coordinate),
                            source=source, source_episode=episode,
                            merged_episode=int(meta['merged_episode_index']), frame=row['frame_index'])
    if frames != 63551 or counts != dict(red=48, green=45, blue=44):
        raise ValueError('137회 학습 프레임 집계 불일치')
    # Model actions are leader targets, encoded using follower calibration.
    training_max = maxima['action']['follower_sdk_integer_tick']
    upper = min(training_max, limits['range_max']-3)
    return dict(episodes=137, frames=frames, counts=dict(counts), maxima=maxima,
        training_max_tick=training_max, training_max_source='action encoded to follower ticks',
        calibration_max_tick=limits['range_max'], margin_ticks=3, command_upper_tick=upper,
        mapping_path=str(mapping), mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),
        source_parquet_sha256=hashes, calibration_sha256=hashlib.sha256(calibration.read_bytes()).hexdigest())


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--datasets',type=Path,required=True)
    p.add_argument('--mapping',type=Path,required=True)
    p.add_argument('--calibration',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=analyze(a.datasets,a.mapping,a.calibration)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='source_parquet_sha256'},ensure_ascii=False))
