"""No device I/O. Prototype streaming contract, NOT an existing RobotController API.

Actual RobotController only accepts stage plans. Live/shadow providers are blocked
until its bounded target/HOLD/unit interface is implemented and field-verified.
"""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import queue
import sys
import threading
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'scripts') not in sys.path: sys.path.insert(0,str(ROOT/'scripts'))
from vla_train import load_config,get_rename_map
from vla_record import load_tasks
from vla_data import JOINTS
from check_vla_cameras import load_config as load_cameras


class HoldRequired(ValueError): pass


def vector(value):
    a=np.asarray(value)
    if a.shape!=(6,) or a.dtype.kind!='f': raise HoldRequired('action/state shape or floating dtype')
    if not np.isfinite(a).all(): raise HoldRequired('action/state nonfinite')
    return a.astype(float,copy=True)


def task_check(task,allow_unseen=False):
    if not isinstance(task,str) or not task.strip(): raise HoldRequired('empty task')
    variants={s for c in load_tasks(ROOT/'configs/vla_tasks.yaml')['combinations'].values() for lang in ('ko','en') for s in c[lang]}
    if task not in variants and not allow_unseen: raise HoldRequired('unregistered task')
    return {'task':task,'unseen_task':task not in variants,'warning':'UNSEEN_TASK' if task not in variants else None}


def rename_observation(obs,train_config):
    mapping=get_rename_map(train_config)
    if not set(mapping)<=obs.keys(): raise HoldRequired('missing camera')
    if set(mapping.values()) & obs.keys(): raise HoldRequired('ambiguous camera keys')
    # Only observation keys; action, task and other keys are never renamed.
    return {mapping.get(k,k) if k.startswith('observation.') else k:v for k,v in obs.items()}


@dataclass(frozen=True)
class Limits:
    cap_deg: float=2.0
    gripper_cap_pp: float=2.0
    hard_deg: float=10.0
    gripper_hard_pp: float=10.0
    clip_window: int=10
    max_clip_rate: float=.5
    max_age_s: float=.25
    max_skew_s: float=.05
    timeout_s: float=1.0

    def __post_init__(self):
        vals=[self.cap_deg,self.gripper_cap_pp,self.hard_deg,self.gripper_hard_pp,self.max_age_s,self.max_skew_s,self.timeout_s,self.max_clip_rate]
        if not np.isfinite(vals).all() or not 0<self.cap_deg<=2 or not 0<self.gripper_cap_pp<=2:
            raise ValueError('cap cannot weaken existing 2-degree / 2-pp limits')
        if self.hard_deg<self.cap_deg or self.gripper_hard_pp<self.gripper_cap_pp or min(self.max_age_s,self.max_skew_s,self.timeout_s)<=0:
            raise ValueError('invalid safety thresholds')
        if type(self.clip_window) is not int or self.clip_window<1 or not 0<=self.max_clip_rate<=1: raise ValueError('invalid clipping window')


@dataclass
class UnitContract:
    """Explicit fixture/calibration contract; never infer units from value ranges."""
    joint_names: list
    policy_arm_unit: str
    controller_arm_unit: str
    policy_gripper_unit: str
    controller_gripper_unit: str
    coordinate: str
    calibration_coordinate: str
    bounds: list
    evidence_status: str='UNKNOWN'

    def check(self):
        if self.evidence_status not in ('SYNTHETIC_FIXTURE','VERIFIED_DECLARATION'): raise HoldRequired('unit evidence unknown')
        if self.joint_names!=JOINTS or self.controller_arm_unit!='degree' or self.policy_arm_unit not in ('degree','radian'):
            raise HoldRequired('joint order or arm units unknown')
        if self.policy_gripper_unit!='percent' or self.controller_gripper_unit!='percent': raise HoldRequired('gripper conversion unverified')
        b=np.asarray(self.bounds,dtype=float)
        if not self.coordinate or self.coordinate!=self.calibration_coordinate or b.shape!=(6,2) or not np.isfinite(b).all() or (b[:,0]>=b[:,1]).any():
            raise HoldRequired('calibration coordinate/bounds mismatch')
        return b

    def convert(self,value):
        self.check();a=vector(value)
        if self.policy_arm_unit=='radian': a[:5]=np.rad2deg(a[:5])
        return a

    def policy_state(self,value):
        self.check();a=vector(value)
        if self.policy_arm_unit=='radian': a[:5]=np.deg2rad(a[:5])
        return a


class ActionGuard:
    def __init__(self,units,limits): self.units=units;self.limits=limits;self.clips=deque(maxlen=limits.clip_window)

    def target(self,raw,last,current):
        bounds=self.units.check();a=self.units.convert(raw);last=vector(last);current=vector(current)
        if (current<bounds[:,0]).any() or (current>bounds[:,1]).any(): raise HoldRequired('current state outside calibration')
        if (a<bounds[:,0]).any() or (a>bounds[:,1]).any(): raise HoldRequired('calibration bounds')
        hard=np.array([self.limits.hard_deg]*5+[self.limits.gripper_hard_pp])
        if (np.abs(a-last)>hard).any() or (np.abs(a-current)>hard).any(): raise HoldRequired('hard jump including first step')
        caps=np.array([self.limits.cap_deg]*5+[self.limits.gripper_cap_pp])
        clipped=np.abs(a-last)>caps;self.clips.append(bool(clipped.any()))
        rate=sum(self.clips)/len(self.clips)
        if len(self.clips)==self.clips.maxlen and rate>self.limits.max_clip_rate: raise HoldRequired('policy-control mismatch: clipping rate')
        return last+np.clip(a-last,-caps,caps),int(clipped.sum()),rate


def validate_packet(packet,now,limits,prestart=False,expected_task=None,cameras=None):
    if set(packet['frames'])!={'wrist','top','oblique'}: raise HoldRequired('camera role set mismatch')
    if expected_task is not None and packet['task']!=expected_task: raise HoldRequired('packet task mismatch')
    state=vector(packet['state']);temps=vector(packet['temperatures'])
    if (temps<0).any() or (temps>=(50 if prestart else 55)).any(): raise HoldRequired('temperature')
    if packet.get('overload') is not False: raise HoldRequired('overload/unknown overload')
    if packet.get('stop_requested') is not False: raise HoldRequired('stop button/unknown stop')
    stamps=np.asarray([packet['state_timestamp']]+[packet['frames'][role]['timestamp'] for role in ('wrist','top','oblique')],dtype=float)
    if not np.isfinite(stamps).all() or (stamps>now).any() or (now-stamps>limits.max_age_s).any(): raise HoldRequired('future/stale timestamps')
    if np.ptp(stamps[1:])>limits.max_skew_s: raise HoldRequired('camera skew')
    obs={'observation.state':state,'task':packet['task']}
    for role,frame in packet['frames'].items():
        im=np.asarray(frame['image'])
        if im.dtype!=np.uint8 or im.ndim!=3 or im.shape[2]!=3 or min(im.shape[:2])<1: raise HoldRequired('image shape/dtype')
        if cameras is not None and im.shape!=(cameras['cameras'][role]['height'],cameras['cameras'][role]['width'],3):
            raise HoldRequired('camera resolution mismatch')
        obs['observation.images.'+role]=im
    return obs


def start_gate(state,evidence,binding,now):
    """For fixture interface only; no arbitrary JSON can enable live mode."""
    if not evidence or evidence.get('dataset_binding')!=binding or evidence.get('audit_status')!='PASS': raise HoldRequired('start audit missing/wrong dataset')
    if evidence.get('unit_status') not in ('SYNTHETIC_FIXTURE','VERIFIED_DECLARATION'): raise HoldRequired('start units unknown')
    ts=evidence.get('checked_at',float('nan'))
    if not np.isfinite(ts) or not 0<=now-ts<=2: raise HoldRequired('stale start evidence')
    mean=vector(evidence['mean']);tol=vector(evidence['tolerance'])
    if (tol<=0).any() or (np.abs(vector(state)-mean)>tol).any(): raise HoldRequired('start outside audited distribution')


class MockPolicy:
    """Recorded replay of supplied numeric chunks; no model import."""
    def __init__(self,chunks,n_action_steps=2): self.chunks=iter(chunks);self.n_action_steps=n_action_steps;self.calls=0
    def predict(self,obs): self.calls+=1;return np.asarray(next(self.chunks))


class MockStreamingController:
    """Proposed port ONLY. Real RobotController has NONE of these streaming methods."""
    physical=False
    def __init__(self,state): self.state=vector(state);self.sent=[];self.holds=[];self.disconnections=[]
    def submit_bounded_target(self,target): self.state=vector(target);self.sent.append(self.state.copy());return self.state.copy()
    def hold_last(self,target,reason): self.holds.append((None if target is None else vector(target),reason))
    def disconnect(self,*,disable_torque):
        if disable_torque is not False: raise AssertionError('torque-off prohibited')
        self.disconnections.append(disable_torque)


def readiness(mode,execute=False,proof=None):
    if mode=='mock': return {'status':'MOCK_ONLY','blockers':[]}
    blocks=['RobotController continuous target/HOLD interface unverified','physical observation provider absent',
            'fresh units/start-distribution/ownership/checkpoint/camera proof unavailable']
    if mode=='live' and not execute: blocks.append('--execute required')
    # JSON flags, old checks and user approval cannot manufacture these interfaces.
    return {'status':'BLOCKED','blockers':blocks,'proof_input_ignored':proof is not None}


def timed_predict(policy,obs,timeout,stop,clock=time.monotonic):
    result=queue.Queue(maxsize=1);started=clock()
    def compute():
        try: result.put((True,policy.predict(obs)))
        except BaseException as e: result.put((False,e))
    thread=threading.Thread(target=compute,daemon=True);thread.start()
    while True:
        if stop(): raise HoldRequired('stop during inference')
        remaining=timeout-(clock()-started)
        if remaining<=0: raise HoldRequired('inference timeout; late action discarded')
        try: ok,value=result.get(timeout=min(remaining,.02))
        except queue.Empty: continue
        if clock()-started>timeout: raise HoldRequired('inference timeout; late action discarded')
        if not ok: raise value
        return value,clock()-started


def run_mock_loop(policy,provider,controller,*,train_config,units,limits,meta_info,task,start_evidence,
                  dataset_binding,max_steps=10,max_time=5,shadow_sim=False,allow_unseen=False,
                  stop=lambda:False,clock=time.monotonic,sleep=time.sleep,cameras=None):
    """All providers must be nonphysical. Production streaming is not connected."""
    if not isinstance(controller,MockStreamingController) or not isinstance(policy,MockPolicy) or getattr(provider,'physical',True):
        raise HoldRequired('mock loop rejects real/unknown providers')
    if type(max_steps) is not int or max_steps<1 or not np.isfinite(max_time) or max_time<=0: raise ValueError('invalid duration')
    fps=meta_info['fps']
    if isinstance(fps,bool) or not np.isfinite(fps) or fps<=0: raise ValueError('invalid dataset fps')
    if type(policy.n_action_steps) is not int or policy.n_action_steps<1: raise ValueError('invalid action consumption')
    records=[];last=None;guard=ActionGuard(units,limits);began=clock();deadline=began;actions=deque();reason='max_steps';last_latency=0
    warning=None;step=0;current=None;raw=None;tick=began
    def logged(value):
        if value is None:return None
        try:
            a=np.asarray(value,dtype=float)
            return np.where(np.isfinite(a),a,None).tolist()
        except (ValueError,TypeError):return 'INVALID_VALUE'
    try:
        warning=task_check(task,allow_unseen);units.check()
        cameras=cameras or load_cameras(ROOT/'configs/vla_cameras.yaml')
        for step in range(max_steps):
            raw=None
            tick=clock()
            if stop(): raise HoldRequired('stop requested')
            if tick-began>=max_time: reason='max_time';break
            packet=provider.read(task);obs=validate_packet(packet,clock(),limits,prestart=(step==0),expected_task=task,cameras=cameras)
            current=vector(packet['state'])
            bounds=units.check()
            if (current<bounds[:,0]).any() or (current>bounds[:,1]).any(): raise HoldRequired('current state outside calibration')
            if step==0:
                start_gate(current,start_evidence,dataset_binding,clock());last=current.copy()
            if not actions:
                obs['observation.state']=units.policy_state(current)
                budget=min(limits.timeout_s,policy.n_action_steps/fps,max_time-(clock()-began))
                if budget<=0: raise HoldRequired('time budget')
                raw,last_latency=timed_predict(policy,rename_observation(obs,train_config),budget,stop,clock)
                chunk=np.asarray(raw)
                if chunk.ndim!=2 or chunk.shape[1]!=6 or len(chunk)<policy.n_action_steps or chunk.dtype.kind!='f' or not np.isfinite(chunk).all():
                    raise HoldRequired('invalid action chunk')
                actions.extend(chunk[:policy.n_action_steps])
            if stop(): raise HoldRequired('stop before send')
            # Fresh observations and temperatures after inference, before every send.
            packet=provider.read(task);validate_packet(packet,clock(),limits,expected_task=task,cameras=cameras);current=vector(packet['state'])
            raw=actions.popleft();target,clips,rate=guard.target(raw,last,current)
            if clock()-began>=max_time: raise HoldRequired('time budget before send')
            if not shadow_sim:
                ack=controller.submit_bounded_target(target)
                if not np.array_equal(vector(ack),target): raise HoldRequired('target acknowledgement mismatch')
                last=target.copy()
            records.append(dict(step=step,elapsed_s=clock()-began,jitter_s=tick-deadline,step_delay_s=clock()-tick,
                inference_s=last_latency,clip_count=clips,clip_rate=rate,raw_action=raw.tolist(),
                sent_action=None if shadow_sim else last.tolist(),proposed_action=target.tolist(),state=current.tolist()))
            deadline+=1/fps
            sleep(max(0,min(deadline-clock(),max_time-(clock()-began))))
    except KeyboardInterrupt: reason='Ctrl+C'
    except Exception as exc: reason=type(exc).__name__+':'+str(exc)
    finally:
        if reason not in ('max_steps','max_time'):
            records.append(dict(step=step,event='HOLD',reason=reason,elapsed_s=clock()-began,
                jitter_s=tick-deadline,step_delay_s=clock()-tick,clip_count=None,clip_rate=None,
                raw_action=logged(raw),sent_action=None,state=logged(current)))
        try: controller.hold_last(last,reason)
        finally: controller.disconnect(disable_torque=False)
    return {'mode':'SHADOW_SIM_SYNTHETIC' if shadow_sim else 'MOCK_SYNTHETIC','reason':reason,
            'outcome':'BOUNDED_LOOP_FINISHED' if reason in ('max_steps','max_time') else 'HOLD',
            'records':records,'task':warning,'video_saved':False,'automatic_success':None,
            'motor_writes':0,'serial_opens':0,'late_thread_note':'Timed-out daemon may finish computing; it has no controller reference and result is discarded.'}


class SmolVLAWrapper:
    """Lazy local-checkpoint wrapper. Never instantiated by mock/live readiness CLI."""
    def __init__(self,checkpoint,source,*,dtype='fp32',device='cuda',n_action_steps=None):
        import os,json
        from vla_data import sha
        evidence=json.loads((ROOT/'configs/vla_inference_source_evidence.json').read_text())
        for file,expected in evidence['official_files'].items():
            if sha(Path(source)/file)!=expected: raise HoldRequired('unverified official inference source')
        cp=Path(checkpoint)
        if not (cp/'config.json').is_file() or not (cp/'model.safetensors').is_file(): raise HoldRequired('local checkpoint required; no downloads')
        src=(Path(source)/'src').resolve()
        for name,module in tuple(sys.modules.items()):
            if name=='lerobot' or name.startswith('lerobot.'):
                f=getattr(module,'__file__',None)
                if f and src not in Path(f).resolve().parents: raise HoldRequired('mixed LeRobot versions; fresh process needed')
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1')
        sys.path.insert(0,str(src))
        import torch
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
        from lerobot.policies.factory import make_pre_post_processors
        self.torch=torch;self.device=device;self.dtype={'fp32':torch.float32,'bf16':torch.bfloat16,'fp16':torch.float16}[dtype]
        self.policy=SmolVLAPolicy.from_pretrained(str(cp),local_files_only=True).to(device).eval()
        self.policy.config.device=device
        self.n_action_steps=n_action_steps or self.policy.config.n_action_steps
        if not 1<=self.n_action_steps<=self.policy.config.chunk_size: raise HoldRequired('n_action_steps exceeds chunk')
        self.policy.config.n_action_steps=self.n_action_steps;self.policy.reset()
        # Caller already maps observation source->model keys. No second rename.
        self.pre,self.post=make_pre_post_processors(self.policy.config,pretrained_path=str(cp),
            preprocessor_overrides={'device_processor':{'device':device},'rename_observations_processor':{'rename_map':{}}},
            postprocessor_overrides={'device_processor':{'device':'cpu'}})

    def predict(self,obs):
        torch=self.torch;batch={}
        for key,value in obs.items():
            if key.startswith('observation.images.'):
                batch[key]=torch.from_numpy(np.asarray(value).copy()).permute(2,0,1).float()/255
            elif key=='observation.state': batch[key]=torch.from_numpy(vector(value).astype(np.float32))
            else: batch[key]=value
        with torch.inference_mode(),torch.autocast(device_type=self.device.split(':')[0],dtype=self.dtype,enabled=self.dtype!=torch.float32):
            chunk=self.policy.predict_action_chunk(self.pre(batch))
            actions=[self.post(chunk[:,i,:]).detach().float().cpu().numpy()[0] for i in range(self.n_action_steps)]
        return np.asarray(actions,dtype=np.float32)
