"""A bounded pan-only camera-direction measurement; never arms or homes a robot."""
import base64
import hashlib
import json
import math
import time
from pathlib import Path

from config_runtime import command_limits, load
from live_start import exact_sync_packet
from start_position_rules import validate_position

JOINTS=tuple(load()['joint_names'])


def validate(sample, baseline=None):
    validate_position(sample['present_raw'])
    for j in JOINTS:
        if sample['torque'][j]!=1 or sample['modes'][j]!=0 or sample['statuses'][j]!=0:
            raise RuntimeError('mode/torque/status: '+j+' '+str({k:sample[k][j] for k in ('torque','modes','statuses')}))
        if sample['temperatures'][j]>=55:
            raise RuntimeError('temperature: '+j+' '+str(sample['temperatures'][j]))
        limit=sample['current_limits'][j]
        if limit<=0 or abs(sample['current_raw'][j])>=limit*.8:
            raise RuntimeError('current: '+j+' '+str(sample['current_raw'][j]))
        torque=sample['torque_limits'][j]
        if torque<=0 or abs(sample['loads'][j])>=torque*.9:
            raise RuntimeError('load: '+j+' '+str(sample['loads'][j]))
        if baseline is not None and j!='shoulder_pan':
            allowed=3 if j=='gripper' else 1
            if abs(sample['state'][JOINTS.index(j)]-baseline['state'][JOINTS.index(j)])>allowed:
                raise RuntimeError('other joint drift: '+j)


def run(io, capture, log, *, delta=6, hold_s=2., clock=time.monotonic, pause=time.sleep):
    if type(delta) is not int or delta!=6:raise ValueError('approved single probe is +6 ticks')
    result=dict(status='HOLD',delta_ticks=delta,torque_register_writes=0,configuration_register_writes=0)
    first=None
    try:
        first=io.snapshot();validate(first)
        goal=first['active_targets']['shoulder_pan'];present=first['present_raw']['shoulder_pan']
        lo,hi=command_limits()['shoulder_pan']
        if abs(goal-present)>6 or not lo<=goal<=hi-delta:raise RuntimeError('pan starting target/range mismatch')
        baseline=[];end=clock()+1.0
        while clock()<end:
            s=io.snapshot();validate(s,first);baseline.append(s);log(dict(event='baseline',sample=s));pause(.1)
        if any(max(s['present_raw'][j] for s in baseline)-min(s['present_raw'][j] for s in baseline)>2 for j in JOINTS):
            raise RuntimeError('baseline not stable within2ticks')
        phases=[]
        for name,target in (('baseline',None),('plus6',goal+delta),('return',goal)):
            if target is not None:io.goal(target,goal,delta)
            rows=[];images=[];end=clock()+hold_s
            while clock()<end:
                s=io.snapshot();validate(s,first);rows.append(s)
                image=capture(name)
                if image:images.append(image)
                log(dict(event=name,sample=s,camera=image));pause(.1)
            phases.append(dict(phase=name,target_raw=goal if target is None else target,samples=rows,images=images))
        result.update(status='PAN_PROBE_COMPLETE',phases=phases,first=first)
    except (KeyboardInterrupt,Exception) as e:
        result['error']=dict(kind=type(e).__name__,reason=str(e))
        # A failed or stale read never licenses a blind "hold confirmed" claim.
        try:
            s=io.snapshot();validate(s)
            current=s['present_raw']['shoulder_pan']
            origin=first['active_targets']['shoulder_pan'] if first else current
            io.goal(current,origin,delta,hold=True)
            final=io.snapshot()
            result['hold']=dict(status='HOLD_CONFIRMED' if final['active_targets']['shoulder_pan']==current else 'HOLD_NOT_CONFIRMED',target_raw=current)
        except (KeyboardInterrupt,Exception) as failure:
            result['hold']=dict(status='HOLD_NOT_CONFIRMED',reason=str(failure))
    return result


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--operator-present',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if not args.execute or not args.operator_present:p.error('operator-present execution required')
    out=args.out.expanduser();out.mkdir(parents=True,exist_ok=False)
    import fcntl,termios,subprocess
    from individual_reply_guard import StrictIndividualReplies,read_target_feedback
    from lerobot.motors import Motor,MotorCalibration,MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    c=load();calpath=Path(c['calibration']).expanduser()
    if hashlib.sha256(calpath.read_bytes()).hexdigest()!=c['calibration_sha256']:raise RuntimeError('calibration hash mismatch')
    cal=json.loads(calpath.read_text());port=Path(c['port']).resolve(strict=True)
    owner=subprocess.run(['fuser',str(port)],capture_output=True,text=True)
    if owner.returncode!=1 or owner.stdout.strip():raise RuntimeError('serial occupied')
    motors={j:Motor(i+1,'sts3215',MotorNormMode.DEGREES if i<5 else MotorNormMode.RANGE_0_100) for i,j in enumerate(JOINTS)}
    bus=FeetechMotorsBus(port=c['port'],motors=motors,calibration={j:MotorCalibration(**cal[j]) for j in JOINTS})
    registers=dict(Present_Position='present_raw',Present_Load='loads',Present_Current='current_raw',Present_Temperature='temperatures',Operating_Mode='modes',Torque_Enable='torque',Status='statuses')
    allowed={tuple(bus.model_ctrl_table['sts3215'][j]) for j in (*registers,'Protection_Current','Torque_Limit')}|{(67,2)}
    original=bus.port_handler.writePort;expected=None;writes=[];current_limits={};torque_limits={}
    def guarded(packet):
        nonlocal expected
        raw=bytes(packet)
        if expected is not None and raw==expected:
            expected=None;writes.append(dict(unix=time.time(),packet_hex=raw.hex(),register='Goal_Position',joint='shoulder_pan'))
        elif not(len(raw)==8 and raw[:2]==b'\xff\xff' and raw[2] in range(1,7) and raw[3:5]==bytes((4,2)) and (raw[5],raw[6]) in allowed and sum(raw[2:])%256==255):
            raise RuntimeError('packet rejected: only approved READ or exact ID1 goal')
        return original(packet)
    bus.port_handler.writePort=guarded
    class IO:
        def snapshot(self):
            s={key:{} for key in registers.values()}
            with StrictIndividualReplies(bus.packet_handler):
                for j in JOINTS:
                    for reg,key in registers.items():s[key][j]=bus.read(reg,j,normalize=False,num_retry=0)
                s['active_targets']=read_target_feedback(bus,JOINTS)
            values=bus._normalize({motors[j].id:s['present_raw'][j] for j in JOINTS})
            s.update(state=[values[motors[j].id] for j in JOINTS],unix=time.time(),current_limits=current_limits,torque_limits=torque_limits)
            return s
        def goal(self,target,origin,delta,hold=False):
            nonlocal expected
            lo,hi=command_limits()['shoulder_pan']
            if type(target) is not int or not lo<=target<=hi or abs(target-origin)>delta:
                raise RuntimeError('pan-only bounded target rejected')
            expected=exact_sync_packet(42,2,{1:target})
            bus.sync_write('Goal_Position',{'shoulder_pan':target},normalize=False,num_retry=0)
            if expected is not None:raise RuntimeError('expected single-axis goal not transmitted')
    camera_path=Path.home()/'.cache/so101-vla-monitor/alignment.json'
    counter=0;last_capture=0
    def capture(phase):
        nonlocal counter,last_capture
        d=json.loads(camera_path.read_text())
        if d.get('phase')!='ready' or not 0<=time.time()-d.get('captured',0)<1.:
            raise RuntimeError('fresh wrist camera absent')
        if d['captured']==last_capture:return None
        last_capture=d['captured'];counter+=1;files={}
        for role in ('end_effector','ceiling_vertical'):
            name=f'{counter:03d}_{phase}_{role}.jpg'
            (out/name).write_bytes(base64.b64decode(d['images'][role],validate=True));files[role]=name
        return dict(captured=d['captured'],files=files)
    result={}
    try:
        bus.connect(handshake=False)
        fcntl.flock(bus.port_handler.ser.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.ioctl(bus.port_handler.ser.fileno(),termios.TIOCEXCL)
        with StrictIndividualReplies(bus.packet_handler):
            for j in JOINTS:
                current_limits[j]=bus.read('Protection_Current',j,normalize=False,num_retry=0)
                torque_limits[j]=bus.read('Torque_Limit',j,normalize=False,num_retry=0)
        with (out/'steps.jsonl').open('x',encoding='utf-8',buffering=1) as f:
            result=run(IO(),capture,lambda r:f.write(json.dumps(r,ensure_ascii=False)+'\n'))
    except (KeyboardInterrupt,Exception) as error:result=dict(status='HOLD',error=dict(kind=type(error).__name__,reason=str(error)))
    finally:
        if bus.port_handler.is_open:bus.disconnect(disable_torque=False)
        result.update(writes=writes,torque_register_writes=0,configuration_register_writes=0,port_closed=not bus.port_handler.is_open,serial_owners_after=subprocess.run(['fuser',str(port)],capture_output=True,text=True).stdout.strip(),calibration_sha256=hashlib.sha256(calpath.read_bytes()).hexdigest(),output=str(out))
        (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('phases','first')},ensure_ascii=False))


if __name__=='__main__':main()
