"""A grasp-only pan offset, disabled until physical direction/TCP verification."""
import hashlib
import math
from pathlib import Path
from config_runtime import load


class GraspOffset:
    def __init__(self, settings=None, height=None):
        self.settings=dict(settings or load()['lateral_correction'])
        self.enabled=self.settings['enabled'];self.height=height
        self.open_cap=load()['gripper']['max_open_points']
        if not math.isfinite(self.settings['offset_deg']) or abs(self.settings['offset_deg'])>self.settings['max_abs_offset_deg'] or not 0<self.settings['max_abs_offset_deg']<=1.5:
            raise ValueError('좌우 보정1.5도 상한 초과')
        if self.enabled and not (self.settings['direction_verified'] and self.settings['geometry_verified']):
            raise ValueError('좌우 방향·TCP 미검증')
        if self.enabled and height is None:
            import numpy as np
            from nominal_kinematics import UrdfKinematics
            source=Path(self.settings['urdf']).expanduser()
            if hashlib.sha256(source.read_bytes()).hexdigest()!=self.settings['urdf_sha256']:raise ValueError('기구학 모델 해시 불일치')
            kin=UrdfKinematics.from_file(source);offset=np.asarray(self.settings['tcp_offset_mm'])/1000
            def height(values):
                names=('Rotation','Pitch','Elbow','Wrist_Pitch','Wrist_Roll')
                t=kin.forward({j:math.radians(v) for j,v in zip(names,values[:5])},'base','gripper')
                return float((t[:3,3]+t[:3,:3]@offset)[2]*1000)
            self.height=height
        self.begin=self.close=None;self.armed=True;self.trough=float('inf')
        self.windows=self.steps=0;self.peak=0.

    def apply(self, target, state, now, future, rate):
        result=list(target);offset=0.
        if not self.enabled:return result,dict(applied_deg=0.,reason='disabled_unverified_direction_or_tcp')
        height=self.height(state);lo,hi=self.settings['height_range_mm']
        eligible=lo<=height<=hi and -32.44<=target[1]<=10.47 and -29.63<=target[2]<=29.37 and target[-1]>=10
        grip=min(target[-1],self.open_cap)
        self.trough=min(self.trough,grip)
        if not self.armed and grip>=self.trough+3:self.armed=True
        if self.close is not None and now>self.close+self.settings['after_close_wall_s']:
            self.begin=self.close=None
        if eligible and self.armed and self.close is None:
            for index,row in enumerate(future,1):
                if index/rate>self.settings['lookahead_wall_s']:break
                if grip-min(row[-1],self.open_cap)>=3:
                    self.begin=now;self.close=now+index/rate;self.armed=False;self.trough=grip;self.windows+=1;break
        if eligible and self.close is not None:
            end=self.close+self.settings['after_close_wall_s'];ramp=self.settings['ramp_s']
            fraction=max(0.,min(1.,(now-self.begin)/ramp,(end-now)/ramp))
            offset=self.settings['offset_deg']*fraction*fraction*(3-2*fraction)
        result[0]+=offset;self.steps+=offset!=0;self.peak=max(self.peak,abs(offset))
        return result,dict(applied_deg=offset,nominal_height_mm=height,eligible=eligible,
                           reason='grasp_window' if offset else 'outside_grasp_window')

    def summary(self):
        return dict(enabled=self.enabled,configured_offset_deg=self.settings['offset_deg'],
                    applied_steps=self.steps,peak_abs_deg=self.peak,windows=self.windows,
                    direction_verified=self.settings['direction_verified'],geometry_verified=self.settings['geometry_verified'])
