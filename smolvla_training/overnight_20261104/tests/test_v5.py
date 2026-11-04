import unittest
from so101_first_action_test_v5_proposal import Supervisor,Profile
import policy_api as api

STATE=[0.]*5+[20.]
HOME=dict(joint_names=list(api.JOINTS),state=STATE,tolerance_deg=5.)
def args():return dict(action=STATE[:],state=STATE[:],previous=STATE[:],torque=[1]*6,modes=[0]*6,
    temperatures=[35.]*6,home=HOME,model_hash_ok=True,units_ok=True,camera_mapping_ok=True,live_confirmed=True,
    observation_age=.1,max_observation_age=.5,inference_seconds=1.,loads=[0.]*6,statuses=[0]*6)
class Tests(unittest.TestCase):
    def test_validated_target_is_still_not_a_write(self):
        r=Supervisor().propose(**args());self.assertEqual(r['decision'],'VALIDATED_TARGET_ONLY');self.assertEqual(r['register_writes'],0)
    def test_nan_inf_shape_and_gripper_fail_closed(self):
        for value in ([float('nan')]*6,[float('inf')]*6,[0.]*5,[0.]*5+[101.]):
            a=args();a['action']=value
            with self.subTest(value=value):self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_partial_torque_blocks_even_hold_goal(self):
        a=args();a['torque'][3]=0;r=Supervisor().propose(**a)
        self.assertEqual(r['decision'],'HOLD_NO_WRITE');self.assertIsNone(r['action'])
    def test_fifty_five_and_start_fifty_stop(self):
        for t in (50.,55.):
            a=args();a['temperatures'][1]=t
            self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_home_or_first_jump_rejected(self):
        for field,value in [('state',[6.]*5+[20.]),('action',[11.]*5+[20.])]:
            a=args();a[field]=value
            self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_missing_hash_units_camera_or_live_confirmation_stops(self):
        for flag in ('model_hash_ok','units_ok','camera_mapping_ok','live_confirmed'):
            a=args();a[flag]=False;self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_stale_and_slow_inference_stop(self):
        for field,value in [('observation_age',.6),('max_observation_age',None),('inference_seconds',3.03)]:
            a=args();a[field]=value;self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_clip_ratio_stops_persistent_policy_control_mismatch(self):
        s=Supervisor(clip_window=3,max_clip_ratio=.5);a=args();a['action']=[2.]*5+[20.]
        self.assertEqual(s.propose(**a)['decision'],'VALIDATED_TARGET_ONLY')
        self.assertEqual(s.propose(**a)['decision'],'VALIDATED_TARGET_ONLY')
        self.assertEqual(s.propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_stop_and_deadline_are_latched(self):
        for change in ({'stop_requested':True},{'elapsed':5.}):
            s=Supervisor();a=args();a.update(change)
            self.assertEqual(s.propose(**a)['decision'],'HOLD_NO_WRITE')
            self.assertEqual(s.propose(**args())['decision'],'HOLD_NO_WRITE')
    def test_speed_bound_keeps_point_five_at_thirty(self):
        a=args();a['action']=[1.]*5+[20.]
        r=Supervisor().propose(**a);self.assertTrue(all(abs(x)<=.5 for x in r['action'][:5]))
    def test_unknown_or_excess_load_and_status_stops(self):
        for field,value in [('loads',None),('loads',[200.]*6),('statuses',[1]*6)]:
            a=args();a[field]=value;self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_nan_timing_cannot_bypass_deadline(self):
        for field in ('elapsed','observation_age','max_observation_age','inference_seconds'):
            a=args();a[field]=float('nan');self.assertEqual(Supervisor().propose(**a)['decision'],'HOLD_NO_WRITE')
    def test_profile_cannot_weaken_limits_or_use_nan(self):
        for p in (Profile(step_cap=1.),Profile(first_action_limit=float('nan')),Profile(start_temperature=60.),Profile(fps=10.)):
            with self.subTest(profile=p),self.assertRaises(ValueError):Supervisor(p)

if __name__=='__main__':unittest.main()
