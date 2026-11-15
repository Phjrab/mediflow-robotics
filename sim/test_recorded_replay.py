"""Same playback function with fake I/O, no motor/model imports."""
import unittest
from config_runtime import load
from recorded_replay import replay,require_goal_only_packet,load_recording
from live_start import exact_sync_packet
import tempfile,json,hashlib
from pathlib import Path
from startup_simulation import SimulatedArm

class ReplayTests(unittest.TestCase):
    def test_goal_only_guard_blocks_torque_pid_calibration_and_acceleration(self):
        for address,width,value in ((40,1,1),(40,1,0),(21,1,16),(9,2,20),(41,1,254),(48,2,500)):
            with self.subTest(address=address,value=value):
                with self.assertRaises(Exception):require_goal_only_packet(exact_sync_packet(address,width,{i:value for i in range(1,7)}))
        require_goal_only_packet(exact_sync_packet(42,2,{i:2000 for i in range(1,7)}))
    def test_recording_contract_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'recording.json';c=load()
            r=dict(task='Put medicine bottle A into the blue basket',fps=30,joints=c['joint_names'],
                calibration_sha256=c['calibration_sha256'],actions=[c['home']['state']]*50)
            p.write_text(json.dumps(r));p.with_suffix('.json.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(len(load_recording(p)['actions']),50)
            p.write_text(p.read_text()+' ')
            with self.assertRaises(ValueError):load_recording(p)
    def make(self,case='replay'):
        c=load();rows=[];io=SimulatedArm(c['home']['state'],{},rows.append,case);io.connect();io.policy_active=True
        actions=[]
        for k in range(180):
            state=list(c['home']['state']);state[2]-=min(6,k/30);state[5]+=min(20,k/10);actions.append(state)
        return io,actions,rows
    def test_recorded_path_completes_at_point33_and_source_end(self):
        io,actions,rows=self.make();result=replay(io,actions,rows.append,.33)
        self.assertEqual(result['status'],'RUN_COMPLETE');self.assertFalse(result['model_used'])
        self.assertGreater(result['steps'],700);self.assertEqual(io.start_writes,[])
        self.assertGreaterEqual(result['elapsed_s'],result['planned_duration_s'])
        sent=[r for r in rows if r['event']=='recorded_goal_transmitted']
        self.assertEqual(sent[-1]['source_action'],actions[-1])
    def test_torque_off_refuses_without_any_write(self):
        io,actions,rows=self.make();io.torque['elbow_flex']=0
        result=replay(io,actions,rows.append)
        self.assertEqual(result['status'],'HOLD');self.assertEqual(io.packet_count,0);self.assertEqual(io.start_writes,[])
    def test_interrupt_temperature_usb_hold(self):
        for case in ('ctrl_c','overheat','usb_disconnect'):
            with self.subTest(case=case):
                io,actions,rows=self.make(case);result=replay(io,actions,rows.append)
                self.assertGreater(result['steps'],0)
                self.assertIn(result['status'],('HOLD','STOPPED'));self.assertEqual(io.start_writes,[])
                if case=='usb_disconnect':self.assertEqual(result['hold']['status'],'HOLD_NOT_CONFIRMED')

if __name__=='__main__':unittest.main()
