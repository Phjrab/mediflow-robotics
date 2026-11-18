"""모터 없이 시작 복구·허용 쓰기·보호 중단·설정 공유를 검증한다."""
import unittest
from config_runtime import load,criteria
import live_start
import direct_live as base
from startup_simulation import SimulatedArm,HOME_TICKS,SCENARIOS

class UnifiedTests(unittest.TestCase):
    def test_startup_recovery_and_no_jump(self):
        c=load()
        for case in ['all_off','partial_off','elbow_plus_1','wrist_plus10','wrist_minus10']:
            with self.subTest(case=case):
                io=SimulatedArm(c['home']['state'],{},lambda r:None,case)
                io.connect();events=[]
                live_start.prepare(io,c['home']['state'],events.append)
                self.assertTrue(all(io.torque.values()))
                for register,values in io.start_writes:
                    if register=='Goal_Position':
                        for joint,value in values.items():self.assertTrue(c['limits'][joint][0]<=value<=c['limits'][joint][1])
                self.assertLessEqual(io.home_motion_speed,10.1)
                self.assertTrue(all(reg in ('Goal_Position','Torque_Enable') for reg,_ in io.start_writes))
                for r in events:
                    if r['event']=='startup_torque_readback':self.assertLessEqual(max(r['goal_error_ticks'].values()),3)
                if case!='all_off' and case!='partial_off':self.assertEqual(io.start_writes,[])
    def test_closing_direction_and_open_release(self):
        from stream_live import GripRelief
        g=GripRelief();g.apply(0,30,0);v=g.apply(0,30,1)
        self.assertEqual(v,26);self.assertEqual(g.apply(50,30,1.1),50)
    def test_thermal_and_status_guards(self):
        c=load();io=SimulatedArm(c['home']['state'],{},lambda r:None,'overheat')
        io.packet_count=20
        for _ in range(100):io.checked()
        with self.assertRaises(base.Stop):io.checked()
    def test_shared_limits(self):
        c=load();self.assertEqual(criteria()['elbow_flex']['command_range_ticks'],c['limits']['elbow_flex'])
        self.assertEqual(c['limits']['elbow_flex'][1],min(3093,c['calibration_ranges']['elbow_flex'][1]-3))
        self.assertNotIn('5AE6085272',c['port'])
    def test_no_configuration_writes_in_start(self):
        import inspect
        source=inspect.getsource(live_start)
        self.assertNotIn("write_start('P_Coefficient'",source)
        self.assertNotIn('configure(',source)

if __name__=='__main__':unittest.main()
