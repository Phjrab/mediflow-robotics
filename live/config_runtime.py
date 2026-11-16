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
    grip=c['gripper']
    if (grip.get('positive_direction')!='open' or not 0<grip.get('max_open_points',100)<50
            or grip.get('opening_load_sign')!=-1 or grip.get('opening_load_threshold_raw')!=400
            or grip.get('opening_load_hold_s')!=.3 or grip.get('overload_recovery_attempts')!=3):
        raise ValueError('승인된 그리퍼 개방 보호 기준 불일치')
    lateral=c.get('lateral_correction',{})
    if (lateral.get('mode')!='model_grasp_offset' or not 0<lateral.get('max_abs_offset_deg',0)<=1.5
            or not math.isfinite(lateral.get('offset_deg',float('nan')))
            or abs(lateral['offset_deg'])>lateral['max_abs_offset_deg']):
        raise ValueError('좌우 보정 승인 범위 불일치')
    if lateral.get('enabled') and not (lateral.get('direction_verified') and lateral.get('geometry_verified')):
        raise ValueError('실제 카메라 좌우 부호·TCP 검증 전 좌우 보정 활성화 금지')
    assert '5AE6085272' not in c['port']
    assert c['temperature']['stop_c'] == 55
    if c.get('communication') != dict(max_retries=3,consecutive_failures=5,no_response_s=.2,
                                     hold_attempts=5,bulk_address=56,bulk_width=15,read_order=[1,2,3,4,6,5]):
        raise ValueError('승인된 통신 재시도·시간·읽기 범위 불일치')
    temperature = c['temperature']
    if (temperature.get('policy') != 'fresh_individual_2s_window'
            or temperature.get('sample_interval_s') != .02
            or temperature.get('window_s') != 2 or temperature.get('continuous_hot_s') != 2
            or temperature.get('median_warning_c') != 50
            or not .02 < temperature.get('max_sample_gap_s', 0) <= .15):
        raise ValueError('승인된 20ms·2초 온도 창 기준 불일치')
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
    if c.get('position_checks') != dict(observed_upper_margin_ticks=3,home_tolerance_ticks=6,
            gripper_tolerance_points=2.,stable_s=.5,max_sample_gap_s=.35,settle_timeout_s=3.,home_attempts=3):
        raise ValueError('승인된 실제 위치·틱 수렴 기준 불일치')
    targets=c['home'].get('target_ticks',{})
    if set(targets)!=set(expected) or any(type(targets[j]) is not int or not c['limits'][j][0]<=targets[j]<=c['limits'][j][1] for j in expected):
        raise ValueError('공통 홈 목표 틱 오류')
    _cached,_stamp=c,stamp
    return c

def path(value):
    return Path(value).expanduser()


def command_limits(c=None):
    c=c or load();result={j:tuple(row) for j,row in c['limits'].items()}
    lo,hi=c['calibration_ranges']['gripper']
    cap=math.floor(lo+c['gripper']['max_open_points']*(hi-lo)/100)
    result['gripper']=(result['gripper'][0],min(result['gripper'][1],cap))
    return result

def contract(c=None):
    c = c or load()
    return dict(schema_version=1, rename_source='checkpoint.policy_preprocessor.json',
                width=640, height=480, channels=3, color_mode='rgb', fps=30,
                warmup_seconds=3, cameras={k:v['path'] for k,v in c['cameras'].items()})

def criteria(c=None):
    c = c or load()
    return {j:dict(command_range_ticks=list(limit), observed_range_ticks=[c['limits'][j][0],min(4095,c['limits'][j][1]+3)],
                   home_target_ticks=c['home']['target_ticks'][j], home_tolerance_ticks=6 if j!='gripper' else None,
                   gripper_tolerance_points=2 if j=='gripper' else None, stable_s=.5,
                   start_action='실제 안전범위 확인 후 틱 수렴; 필요 시 저속 홈 이동 최대3회')
            for j,limit in command_limits(c).items()}
