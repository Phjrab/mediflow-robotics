"""Bench combined feedback and the live retry path; every register write denied."""
import argparse,hashlib,json,time,statistics
from pathlib import Path
from config_runtime import load
from communication_retry import CommunicationPolicy
from direct_live import read_sync_replies
from individual_reply_guard import StrictIndividualReplies
from servo_thermal_readonly import require_unowned_port
from thermal_read import read_temperatures

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--layout',choices=('combined','split'),default='combined')
    p.add_argument('--order',choices=('normal','id5_last'),default='normal')
    p.add_argument('--duration',type=float,default=2.4);a=p.parse_args()
    if not 2<=a.duration<=10:p.error('2~10초 읽기만 허용')
    c=load();cal=Path(c['calibration']).expanduser()
    if hashlib.sha256(cal.read_bytes()).hexdigest()!=c['calibration_sha256']:raise RuntimeError('보정 해시 불일치')
    portpath=Path(c['port']).resolve(strict=True);require_unowned_port(portpath)
    from lerobot.motors import Motor,MotorCalibration,MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    joints=c['joint_names'];ids={j:i+1 for i,j in enumerate(joints)}
    read_ids=dict(sorted(ids.items(),key=lambda x:(x[1]==5,x[1]))) if a.order=='id5_last' else ids
    bus=FeetechMotorsBus(port=c['port'],motors={j:Motor(i+1,'sts3215',MotorNormMode.DEGREES if i<5 else MotorNormMode.RANGE_0_100) for i,j in enumerate(joints)},
        calibration={j:MotorCalibration(**v) for j,v in json.loads(cal.read_text()).items()})
    a.out.mkdir(parents=True,exist_ok=False);result=dict(status='HOLD',live='NOT_RUN',motor_register_writes=0)
    with (a.out/'wire.jsonl').open('x') as f:
        def log(row):f.write(json.dumps(row)+'\n');f.flush()
        policy=CommunicationPolicy(joints,c['communication'],log)
        original=bus.port_handler.writePort
        def only_reads(packet):
            r=bytes(packet)
            if not (len(r)>=8 and r[:2]==b'\xff\xff' and sum(r[2:])%256==255 and (
                (r[4]==0x82 and r[2]==254 and tuple(r[5:7]) in ((33,38),(33,8),(56,15)) and list(r[7:-1])==list(read_ids.values())) or
                (len(r)==8 and r[4]==2 and r[2] in range(1,7) and (r[5],r[6]) in ((33,38),(33,8),(56,15),(63,1))))):
                raise RuntimeError('READ 이외 송신 거부')
            return original(packet)
        bus.port_handler.writePort=only_reads
        cycles=[];previous={};modes=torque=positions=temps=None
        try:
            bus.connect(handshake=False);policy.attach(bus.port_handler)
            begin=time.monotonic()
            while time.monotonic()-begin<a.duration:
                start=time.monotonic()
                def block(address,width):
                    def once():
                        code=bus.packet_handler.syncReadTx(bus.port_handler,address,width,list(read_ids.values()),6)
                        if code:raise RuntimeError(f'sync read transmit {code}')
                        return read_sync_replies(bus.packet_handler,bus.port_handler,read_ids,address,width,policy)
                    return policy.perform(list(ids.values()),f'read:{address}:{width}',once,port=bus.port_handler,packet_timeout_s=None)
                if a.layout=='combined':
                    data=block(33,38);mode={j:r[:8] for j,r in data.items()};data={j:r[23:38] for j,r in data.items()}
                else:data=block(56,15);mode=block(33,8)
                modes={j:r[0] for j,r in mode.items()};torque={j:r[7] for j,r in mode.items()}
                positions={j:int.from_bytes(r[:2],'little') for j,r in data.items()}
                normalized=bus._normalize({ids[j]:v for j,v in positions.items()})
                policy.update_pose(dict(state=[normalized[ids[j]] for j in joints],present_raw=positions))
                temps,_=read_temperatures(bus,ids,{j:r[7] for j,r in data.items()},StrictIndividualReplies,
                    last_requests=previous,retry=policy)
                if any(r[9] for r in data.values()) or max(temps.values())>=55:raise RuntimeError('상태/온도 중단')
                cycles.append(time.monotonic()-start)
                log(dict(event='bulk_readonly_cycle',positions_raw=positions,temperatures=temps,
                    voltage_v={j:r[6]/10 for j,r in data.items()},torque=torque,modes=modes))
            result['status']='BULK_TRANSPORT_READONLY_COMPLETE'
        except Exception as e:result.update(error=dict(kind=type(e).__name__,reason=str(e)))
        finally:
            if bus.port_handler.is_open:bus.disconnect(disable_torque=False)
            policy.detach();require_unowned_port(portpath)
            result.update(port_closed=not bus.port_handler.is_open,cycles=len(cycles),layout=a.layout,read_order=list(read_ids.values()),
                mean_work_s=statistics.mean(cycles) if cycles else None,max_work_s=max(cycles,default=None),
                last_positions_raw=positions,temperatures=temps,torque=torque,modes=modes,communication=policy.summary())
    (a.out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    return 0 if result['status']=='BULK_TRANSPORT_READONLY_COMPLETE' else 1

if __name__=='__main__':raise SystemExit(main())
