import copy
import unittest
from pan_camera_probe import JOINTS,run,validate


class Clock:
    def __init__(self):self.t=0.
    def __call__(self):return self.t
    def sleep(self,x):self.t+=x


class IO:
    def __init__(self):
        self.writes=[]
        self.sample=dict(present_raw=dict.fromkeys(JOINTS,2100),active_targets=dict.fromkeys(JOINTS,2100),state=[0]*6,
                         torque=dict.fromkeys(JOINTS,1),modes=dict.fromkeys(JOINTS,0),statuses=dict.fromkeys(JOINTS,0),
                         temperatures=dict.fromkeys(JOINTS,33),current_limits=dict.fromkeys(JOINTS,250),
                         torque_limits=dict.fromkeys(JOINTS,500),current_raw=dict.fromkeys(JOINTS,3),loads=dict.fromkeys(JOINTS,20))
        self.sample['present_raw']['gripper']=1000;self.sample['active_targets']['gripper']=1000
    def snapshot(self):return copy.deepcopy(self.sample)
    def goal(self,target,origin,delta,hold=False):
        self.writes.append(('shoulder_pan',target));self.sample['present_raw']['shoulder_pan']=target;self.sample['active_targets']['shoulder_pan']=target


class PanProbeTests(unittest.TestCase):
    def test_only_pan_plus6_and_return(self):
        c=Clock();io=IO();result=run(io,lambda phase:{'phase':phase},lambda r:None,clock=c,pause=c.sleep)
        self.assertEqual(result['status'],'PAN_PROBE_COMPLETE');self.assertEqual(io.writes,[('shoulder_pan',2106),('shoulder_pan',2100)])
        self.assertEqual(result['torque_register_writes'],0)
    def test_no_probe_if_off_or_status_bad(self):
        for key,value in [('torque',0),('statuses',32),('temperatures',55)]:
            c=Clock();io=IO();io.sample[key]['gripper']=value
            result=run(io,lambda _:None,lambda _:None,clock=c,pause=c.sleep)
            self.assertEqual(result['status'],'HOLD');self.assertFalse(io.writes)
    def test_other_joint_drift_is_a_stop(self):
        io=IO();baseline=io.snapshot();io.sample['state'][2]=1.01
        with self.assertRaisesRegex(RuntimeError,'other joint drift'):validate(io.sample,baseline)
    def test_camera_loss_holds_current_pan(self):
        c=Clock();io=IO()
        def fail(_):raise RuntimeError('fresh wrist camera absent')
        result=run(io,fail,lambda _:None,clock=c,pause=c.sleep)
        self.assertEqual(result['hold']['status'],'HOLD_CONFIRMED');self.assertEqual(io.writes,[('shoulder_pan',2100)])
    def test_load_and_current_limits(self):
        for key,value in [('loads',450),('current_raw',200)]:
            io=IO();io.sample[key]['shoulder_pan']=value
            with self.assertRaises(RuntimeError):validate(io.snapshot())
    def test_unapproved_delta_rejected(self):
        for value in (5,7,-6):
            with self.assertRaises(ValueError):run(IO(),lambda _:None,lambda _:None,delta=value)


if __name__=='__main__':unittest.main()
