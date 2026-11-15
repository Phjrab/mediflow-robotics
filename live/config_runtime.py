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
    if c['temperature'].get('hot_confirmation_reads', 2) != 2 or c['temperature'].get('emergency_c', 70) != 70:
        raise ValueError('승인된 온도 재확인 횟수·기준 불일치')
    tracking = c.get('tracking_compensation', {})
    axes = tracking.get('enabled_axes', [])
    if not set(axes) <= set(c['joint_names'][:5]) or len(axes) != len(set(axes)):
        raise ValueError('추종 보상 축 오류; 그리퍼는 조임 완화 경로 사용')
    if tracking:
        for key in ('max_offset_deg', 'integral_gain_per_s', 'max_accumulation_deg_s',
                    'decay_per_s', 'settle_s', 'error_deadband_deg',
                    'max_reference_speed_deg_s', 'reset_gap_s'):
            value = tracking[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('추종 보상 수치 오류: ' + key)
        if tracking['max_offset_deg'] > 3:
            raise ValueError('추종 보상 승인 상한 3도 초과')
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
