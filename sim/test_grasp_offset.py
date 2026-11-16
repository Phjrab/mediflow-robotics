"""Only a measured image-plane grasp-window offset may change shoulder_pan."""
import unittest
from config_runtime import load
from grasp_offset import GraspOffset


class GraspOffsetTests(unittest.TestCase):
    def setup_guard(self,**changes):
        settings={**load()['lateral_correction'],'enabled':True,'direction_verified':True,
                  'scale_verified':True,'offset_deg':4*360/4095,**changes}
        return GraspOffset(settings,height=lambda state:state[0])

    def test_disabled_physical_bias_unchanged(self):
        guard=GraspOffset({**load()['lateral_correction'],'enabled':False});target=[3,-23,12,66,-5,45]
        actual,why=guard.apply(target,[130]*6,0,[],9.9)
        self.assertEqual(actual,target);self.assertEqual(why['applied_deg'],0)

    def test_unverified_direction_or_scale_blocks_activation(self):
        for flags in ({'direction_verified':False},{'scale_verified':False}):
            with self.assertRaises(ValueError):self.setup_guard(**flags)

    def test_pan_only_in_lookahead_closing_height_window(self):
        guard=self.setup_guard();target=[3,-23,12,66,-5,45]
        future=[target[:-1]+[x] for x in (45,44,40,38)]
        guard.apply(target,[130]*6,0,future,9.9)
        actual,why=guard.apply(target,[130]*6,.2,future,9.9)
        self.assertGreater(why['applied_deg'],0);self.assertLessEqual(why['applied_deg'],1.5)
        self.assertEqual(actual[1:],target[1:])
        self.assertEqual(guard.apply(target,[180]*6,.25,future,9.9)[0],target)
        self.assertEqual(guard.apply(target,[130]*6,2,future,9.9)[0],target)

    def test_no_closure_in_one_second_and_non_grasp_pose_unchanged(self):
        target=[3,-23,12,66,-5,45];future=[target]*15+[target[:-1]+[30]]
        guard=self.setup_guard()
        for t in (0,.2,.6):self.assertEqual(guard.apply(target,[130]*6,t,future,9.9)[0],target)
        home=load()['home']['state'];guard=self.setup_guard()
        self.assertEqual(guard.apply(home,[130]*6,0,[home[:-1]+[0]],9.9)[0],home)

    def test_limit_and_nan_rejected(self):
        for value in (1.51,float('nan')):
            with self.assertRaises(ValueError):self.setup_guard(offset_deg=value)

    def test_clipped_opening_targets_are_not_a_real_closure(self):
        target=[3,-23,12,66,-5,61];guard=self.setup_guard()
        for t in (0,.2,.4):
            self.assertEqual(guard.apply(target,[130]*6,t,[target[:-1]+[55]]*8,9.9)[0],target)
        self.assertEqual(guard.windows,0)

    def test_measured_plus6_configuration_and_bad_hash_rejected(self):
        self.assertAlmostEqual(load()['lateral_correction']['offset_deg'],6*360/4095)
        guard=GraspOffset()
        self.assertTrue(guard.enabled);self.assertFalse(guard.summary()['tcp_physically_calibrated'])
        with self.assertRaises(ValueError):self.setup_guard(measurement_sha256='0'*64)

    def test_standard_library_nominal_height_matches_reference_fk(self):
        pose=[3.7482517482517483,-22.727272727272716,12.811188811188822,67.2087912087912,-5.230769230769228,36.92695003894514]
        self.assertAlmostEqual(GraspOffset().height(pose),132.7359064025272,places=6)

    def test_setup_time_does_not_expire_policy_loop(self):
        from unittest.mock import patch
        from startup_simulation import SimulatedArm
        from stream_live import stream
        home=load()['home']['state'];io=SimulatedArm(home,{},lambda _:None,'camera_dry')
        def setup():
            io.pause(2.)
            return GraspOffset()
        with patch('stream_live.GraspOffset',setup):
            result=stream(io,lambda *args:([list(home)]*50,.02),lambda _:None,.2,.33)
        self.assertEqual(result['status'],'RUN_COMPLETE');self.assertGreater(result['steps'],0)

    def test_same_stream_sends_grasp_offset_only_to_pan(self):
        from startup_simulation import SimulatedArm
        from stream_live import stream
        pose=[3.5,-23.,13.,67.,-5.,45.]
        io=SimulatedArm(pose,{},lambda _:None,'camera_dry');io.policy_active=True;rows=[]
        def policy(frames,state,origin,delay):
            return [pose[:-1]+[45. if origin+i<10 else 30.] for i in range(50)],.02
        result=stream(io,policy,rows.append,3.,.33)
        self.assertEqual(result['status'],'RUN_COMPLETE');self.assertGreater(result['lateral_correction']['applied_steps'],0)
        self.assertAlmostEqual(result['lateral_correction']['peak_abs_deg'],6*360/4095)
        packets=[r for r in rows if r.get('event')=='goal_transmitted']
        base=io.plan(pose)['wire_raw']['shoulder_pan']
        self.assertEqual(max(r['wire_raw']['shoulder_pan']-base for r in packets),6)
        for row in packets:
            self.assertEqual(row['model'][1:5],pose[1:5])
            self.assertEqual(row['wire_raw']['wrist_roll'],io.plan(pose)['wire_raw']['wrist_roll'])


if __name__=='__main__':unittest.main()
