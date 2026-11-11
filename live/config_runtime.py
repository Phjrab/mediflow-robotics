"""공통 설정. JSON은 YAML의 부분집합이며 추가 패키지 없이 읽는다."""
import json
import os
import math
from pathlib import Path

PATH = Path(os.environ.get('SO101_CONFIG', Path(__file__).with_name('config.yaml')))
_cached = None
_stamp = None

def load():
    global _cached,_stamp
    stamp=PATH.stat().st_mtime_ns
    if _cached is not None and stamp==_stamp:return _cached
    text=PATH.read_text(encoding='utf-8')
    try:import yaml
    except ImportError:c=json.loads(text)
    else:c=yaml.safe_load(text)
    assert c['port'].startswith('/dev/serial/by-id/') and '5AE6058306' in c['port']
    assert '5AE6085272' not in c['port']
    assert c['temperature']['stop_c'] == 55
    assert 0 < c['speed'] <= 1 and c['control_hz'] == 60
    assert len(c['joint_names']) == len(c['home']['state']) == 6
    expected=['shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper']
    if c['joint_names']!=expected or c['home']['joint_names']!=expected:raise ValueError('관절 순서 불일치')
    if not all(math.isfinite(v) for v in c['home']['state']) or not 0<=c['home']['state'][5]<=100:raise ValueError('홈 수치 형식 오류')
    if any(not 0<=lo<hi<=4095 for lo,hi in c['limits'].values()):raise ValueError('명령 가동범위 오류')
    _cached,_stamp=c,stamp
    return c

def path(value):
    return Path(value).expanduser()

def contract(c=None):
    c = c or load()
    return dict(schema_version=1, rename_source='checkpoint.policy_preprocessor.json',
                width=640, height=480, channels=3, color_mode='rgb', fps=30,
                warmup_seconds=3, cameras={k:v['path'] for k,v in c['cameras'].items()})

def criteria(c=None):
    c = c or load()
    return {j:dict(command_range_ticks=limit, start_action='현재 목표 일치 후 범위 안쪽·홈으로 저속 이동',
                   home_tolerance=c['home']['tolerance_deg']) for j,limit in c['limits'].items()}
