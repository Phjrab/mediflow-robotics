"""Verify fresh 20ms thermal reads, with all motor-register writes blocked."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from config_runtime import load
from individual_reply_guard import StrictIndividualReplies
from servo_thermal_readonly import require_unowned_port
from thermal_read import read_temperatures
from thermal_window import ThermalWindow


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--duration',type=float,default=2.4)
    a=p.parse_args()
    if not 2<=a.duration<=10:p.error('읽기 진단 2~10초만 허용')
    c=load();calpath=Path(c['calibration']).expanduser()
    if hashlib.sha256(calpath.read_bytes()).hexdigest()!=c['calibration_sha256']:
        raise RuntimeError('보정 해시 불일치; 장치 미연결')
    require_unowned_port(Path(c['port']).resolve(strict=True))
    from lerobot.motors import Motor,MotorCalibration,MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    calibration=json.loads(calpath.read_text())
    joints=c['joint_names'];ids={j:i+1 for i,j in enumerate(joints)}
    bus=FeetechMotorsBus(port=c['port'],motors={j:Motor(ids[j],'sts3215',MotorNormMode.DEGREES if i<5 else MotorNormMode.RANGE_0_100) for i,j in enumerate(joints)},
                        calibration={j:MotorCalibration(**calibration[j]) for j in joints})
    locations={(63,1),(65,1),(40,1),(62,1),(60,2),(69,2)}
    write_port=bus.port_handler.writePort
    attempts=0
    def read_only(packet):
        nonlocal attempts
        raw=bytes(packet)
        if (len(raw)!=8 or raw[:2]!=b'\xff\xff' or raw[2] not in range(1,7)
                or raw[3:5]!=bytes((4,2)) or (raw[5],raw[6]) not in locations or sum(raw[2:])%256!=255):
            raise RuntimeError('승인 READ 이외 패킷 차단')
        attempts+=1
        return write_port(packet)
    bus.port_handler.writePort=read_only
    previous={};window=ThermalWindow(joints,c['temperature']);intervals=[];cycles=0
    result=dict(status='HOLD',live='NOT_RUN',motor_register_writes=0)
    a.out.mkdir(exist_ok=False,parents=True)
    with (a.out/'samples.jsonl').open('x',encoding='utf-8') as f:
        def log(row):f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
        try:
            bus.connect(handshake=False)
            with StrictIndividualReplies(bus.packet_handler):
                result['initial_torque']={j:bus.read('Torque_Enable',j,normalize=False,num_retry=0) for j in joints}
                statuses={j:bus.read('Status',j,normalize=False,num_retry=0) for j in joints}
                if any(statuses.values()):raise RuntimeError('모터 상태 오류')
            begin=time.monotonic()
            while time.monotonic()-begin<a.duration:
                traces=[]
                values,_=read_temperatures(bus,ids,dict.fromkeys(joints,0),StrictIndividualReplies,
                    traces=traces,last_requests=previous,minimum_interval_s=.02)
                decisions={r['joint']:window.observe_axis(r['joint'],r['observed_monotonic_s'],r['value_c']) for r in traces}
                intervals.extend(r['request_interval_s'] for r in traces if r['request_interval_s'] is not None)
                context={}
                if values['shoulder_lift']>=55:
                    with StrictIndividualReplies(bus.packet_handler) as strict:
                        context={name:bus.read(name,'shoulder_lift',normalize=False,num_retry=0)
                                 for name in ('Present_Voltage','Present_Load','Present_Current','Torque_Enable')}
                        context['reply_trace']=list(strict.history)
                log(dict(event='fresh_window_readonly',sequence=cycles,temperatures=values,
                         decisions=decisions,reply_trace=traces,shoulder_lift_context=context))
                cycles+=1
                if any(row['hold'] for row in decisions.values()):
                    result['reason']='2초 온도 창 HOLD';break
            else:result['status']='FRESH_WINDOW_READONLY_COMPLETE'
        except Exception as error:
            result['error']=dict(kind=type(error).__name__,reason=str(error))
        finally:
            if bus.port_handler.is_open:bus.disconnect(disable_torque=False)
            result.update(port_closed=not bus.port_handler.is_open,read_packets=attempts,cycles=cycles,
                interval_min_s=min(intervals,default=None),interval_max_s=max(intervals,default=None),
                thermal_protection=window.summary(),calibration_sha256=hashlib.sha256(calpath.read_bytes()).hexdigest())
            require_unowned_port(Path(c['port']).resolve(strict=True))
    (a.out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='FRESH_WINDOW_READONLY_COMPLETE' else 1


if __name__=='__main__':raise SystemExit(main())
