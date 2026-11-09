import contextlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from data_motion import Axis
from first_trial_core import Hold, JOINTS
from servo_step_response import (Cadence, SEQUENCE, WORK_SEQUENCE, bounded_goal, main, phase_metrics,
                                 response_bytes, run_motion, run_site, single_goal)
from training_follow_response import crossing_frames


class Clock:
    now = 0.
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class StepResponseTests(unittest.TestCase):
    def test_default_mode_is_plan_only(self):
        with patch("sys.stdout") as output:
            self.assertEqual(main([]),0)
        self.assertIn("PLAN_ONLY",output.write.call_args_list[0].args[0])

    def test_single_goal_packet_denies_other_axis_address_and_value(self):
        head = bytes((255,255,254,7,131,42,2,3,0,8))
        packet = head+bytes(((~sum(head[2:]))&255,))
        self.assertTrue(single_goal(packet,3,2048))
        self.assertFalse(single_goal(packet,2,2048))
        self.assertFalse(single_goal(packet,3,2049))
        self.assertFalse(single_goal(packet[:-1]+b"\0",3,2048))

    def test_block_status_checksum_and_id_fail_closed(self):
        payload=bytes(15)
        def packet(status):
            head=bytes((255,255,3,17,status))+payload
            return head+bytes(((~sum(head[2:]))&255,))
        self.assertEqual(response_bytes(packet(0),0,3,15),payload)
        for source, code, mid, size in ((packet(1),0,3,15),(packet(0),-6,3,15),
                                         (packet(0),0,2,15),(packet(0),0,3,14)):
            with self.assertRaises(Hold):
                response_bytes(source,code,mid,size)

    def test_cadence_rejects_missing_50hz_before_goal(self):
        clock=Clock()
        cadence=Cadence(clock,clock.sleep)
        cadence.sample(lambda:None)
        with self.assertRaisesRegex(Hold,"50Hz"):
            cadence.sample(lambda:clock.sleep(.010))

    def test_ramp_rounds_inside_speed_and_step_limits(self):
        axis=Axis(0,2000,-100,100)
        self.assertAlmostEqual(axis.decode(bounded_goal(axis,0,6,.017,(-100,100))),.2)
        self.assertAlmostEqual(axis.decode(bounded_goal(axis,0,-6,1.,(-100,100))),-2)

    def test_signed_response_metrics(self):
        axis=Axis(0,2000,-100,100)
        rows=[dict(seconds=s,position_ticks=p,position_deg=axis.decode(p),goal_ticks=g,
                   load=0,current=0,temperature_c=30)
              for s,p,g in ((0.,1000,990),(.1,1000,985),(.2,996,970),
                            (.3,975,970),(1.8,972,970),(2.,972,970))]
        result=phase_metrics(rows,-3,1000,970,axis,True)
        self.assertEqual(result["steady_error_ticks"],-2)
        self.assertAlmostEqual(result["steady_error_deg"],-.2)
        self.assertAlmostEqual(result["time_to_63_seconds"],.2+(.3-.2)*(18.9-4)/(25-4))
        self.assertEqual(result["deadzone_estimate_ticks"],15)

    def test_mock_full_sequence_writes_only_selected_axis(self):
        clock=Clock()
        cadence=Cadence(clock,clock.sleep)
        axes={j:Axis(0,2000,-100,100) for j in JOINTS}
        axes["gripper"]=Axis(0,1000,0,100)
        positions=dict.fromkeys(JOINTS,1000)
        positions["gripper"]=500
        guard=SimpleNamespace(goal_once=contextlib.nullcontext)
        writes=[]
        def write(name, values, normalize, num_retry):
            self.assertEqual((name,set(values),normalize,num_retry),("Goal_Position",{"elbow_flex"},False,0))
            self.assertEqual(values["elbow_flex"],guard.expected)
            self.assertLessEqual(abs(values["elbow_flex"]-positions["elbow_flex"]),2)
            writes.append(values["elbow_flex"])
            positions.update(values)
        def sample():
            return dict(ticks=dict(positions), state=[axes[j].decode(positions[j]) for j in JOINTS],
                        loads=dict.fromkeys(JOINTS,0), currents=dict.fromkeys(JOINTS,0),
                        temperatures=dict.fromkeys(JOINTS,30))
        rows, metrics=[],[]
        with patch("servo_step_response.block",return_value={j:n.to_bytes(2,"little") for j,n in positions.items()}):
            run_motion(SimpleNamespace(sync_write=write),guard,cadence,axes,"elbow_flex",sample,[None],rows,metrics)
        self.assertEqual([m["offset_deg"] for m in metrics],list(SEQUENCE))
        self.assertEqual((min(writes),max(writes),writes[-1]),(940,1060,1000))
        self.assertTrue(all(m["final_reach_ratio"]==1 for m in metrics if m["offset_deg"]))

    def test_home_to_work_negative_only_and_back_to_same_raw_home(self):
        clock=Clock()
        cadence=Cadence(clock,clock.sleep)
        axes={j:Axis(0,2000,-100,100) for j in JOINTS}
        axes["gripper"]=Axis(0,1000,0,100)
        positions=dict.fromkeys(JOINTS,1000)
        positions.update(elbow_flex=1960,gripper=500)
        home=dict(joint_names=list(JOINTS),state=[0.,0.,96.,0.,0.,50.],tolerance_deg=5.)
        guard=SimpleNamespace(goal_once=contextlib.nullcontext)
        def write(name, values, normalize, num_retry):
            self.assertEqual((name,set(values),normalize,num_retry),("Goal_Position",{"elbow_flex"},False,0))
            positions.update(values)
        def sample():
            return dict(ticks=dict(positions),state=[axes[j].decode(positions[j]) for j in JOINTS],
                        loads=dict.fromkeys(JOINTS,0),currents=dict.fromkeys(JOINTS,0),
                        temperatures=dict.fromkeys(JOINTS,30))
        rows,metrics,report=[],[],{}
        def active(*args):
            return {j:n.to_bytes(2,"little") for j,n in positions.items()}
        with patch("servo_step_response.block",side_effect=active):
            run_site(SimpleNamespace(sync_write=write),guard,cadence,axes,"elbow_flex",sample,home,"B",rows,metrics,report)
        self.assertEqual([m["offset_deg"] for m in metrics],list(WORK_SEQUENCE))
        self.assertTrue(report["returned_to_home"])
        self.assertEqual(positions["elbow_flex"],1960)
        self.assertEqual(report["start_route"]["site_ticks"],1892)
        self.assertLessEqual(max(r["goal_ticks"] for r in rows if type(r["phase"]) is int),1892)

    def test_training_response_crossing_and_censor(self):
        actions=[0.]*3+[4.]*18
        state=[0.]*3+[1.,2.,3.]+[4.]*15
        self.assertEqual(crossing_frames(actions,state),([2],0,0))
        self.assertEqual(crossing_frames(actions,[0.]*21),([],1,0))


if __name__=="__main__":
    unittest.main()
