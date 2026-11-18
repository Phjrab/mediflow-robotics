"""Replay saved six-axis feedback through startup and the policy loop, no devices."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
from config_runtime import load
from startup_simulation import SimulatedArm, VirtualExecutor
from stream_live import start_policy_session
import direct_live as base


def replay(source):
    cycles=[];first_goal=None
    with source.open() as f:
        for line in f:
            row=json.loads(line)
            if row.get('event')=='thermal_cycle':cycles.append(row)
            elif row.get('event')=='wire_tx' and row.get('instruction')==131 and row.get('address')==42 and first_goal is None:
                first_goal=row['monotonic_s']
    start=max(r['monotonic_s'] for r in cycles if r['monotonic_s']<first_goal)
    rows=[r for r in cycles if r['monotonic_s']>=start];times=[r['monotonic_s']-start for r in rows]
    events=[];used=set();c=load()
    class ReplayArm(SimulatedArm):
        def checked(self):
            index=max(0,bisect.bisect_right(times,self.clock())-1);r=rows[index];used.add(index)
            raw={j:int.from_bytes(bytes.fromhex(data)[:2],'little') for j,data in r['feedback_bytes_hex'].items()}
            def signed_load(data):
                value=int.from_bytes(bytes.fromhex(data)[4:6],'little')
                return -(value&1023) if value&1024 else value
            sample=dict(present_raw=raw,state=self.normalize(raw),temperatures=r['temperature_individual_c'],
                        statuses=r['statuses'],temperature_protection=r['temperature_protection'],
                        modes=dict.fromkeys(base.JOINTS,0),torque=dict.fromkeys(base.JOINTS,1),active_targets=self.target.copy(),
                        loads={j:signed_load(data) for j,data in r['feedback_bytes_hex'].items()})
            base.feedback_gate(sample)
            return sample
        start_snapshot=checked
    io=ReplayArm(c['home']['state'],{},events.append,'camera_dry')
    io.pool_factory=lambda **kw:VirtualExecutor(io.clock,False)
    def policy(*args):return [c['home']['state'][:] for _ in range(50)],.02
    result=start_policy_session(io,policy,c['home']['state'],events.append,.5,.33)
    ready=next((r for r in events if r['event']=='startup_ready'),None)
    return dict(passed=bool(ready and result['steps']>0),source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                feedback='unaltered saved six-axis position, temperature and status samples',source_cycles=len(rows),used_cycles=len(used),
                policy='deterministic mock; real model checked separately in camera dry-run',physical_motor_writes=0,
                startup_ready=ready,result=result,events=events)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=replay(a.source);a.out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k!='events'},ensure_ascii=False));raise SystemExit(0 if r['passed'] else 1)
