import copy
import unittest
from first_trial_core import JOINTS,Hold,FirstMotionPlan,FirstTrialAdapter,target_gate,Limits
from controller_bridge import load_controller
from work_segment_limits import WorkSegmentLimits
from paced_tracking import plan_actions,ProgressMonitor,TrajectoryCursor,TrackingThresholds
from paced_trial import PacedTrialAdapter
from paced_replay import SimulatedIO,immediate,run_case

BOUNDS=[(-180.,180.)]*5+[(0.,100.)]
Q=[360/4095]*5+[.0612]

def prediction(step=.1):
    return dict(observation_state=[0.]*5+[10.],raw_actions=[[i*step]*5+[10.] for i in range(50)],inference_seconds=0.,motor_quantum=Q)

class PlanTests(unittest.TestCase):
    def test_speed_and_encoder_margin_for_every_interpolated_step(self):
        p=prediction(.3);plan=plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
        previous=p['observation_state']
        for row in plan['points']:
            for i in range(5):self.assertLessEqual(abs(row['waypoint'][i]-previous[i]),20/30-2*Q[i]+1e-8)
            previous=row['waypoint']
        self.assertEqual(previous,p['raw_actions'][-1]);self.assertEqual(plan['raw_count'],50)
    def test_fast_policy_is_time_scaled_without_dropping_last_point(self):
        p=prediction(3.2);plan=plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
        self.assertGreater(plan['planned_points'],50);self.assertEqual(plan['points'][-1]['waypoint'],p['raw_actions'][-1])
        self.assertGreater(plan['minimum_execution_seconds'],50/30)
    def test_source_is_unchanged_and_points_not_aliased(self):
        p=prediction();before=copy.deepcopy(p);plan=plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
        plan['points'][0]['waypoint'][0]=123
        self.assertEqual(p,before)
    def test_policy_first_target_and_discontinuity_are_distinct(self):
        for first,code in ((True,'POLICY_FIRST_TARGET'),(False,'POLICY_DISCONTINUITY')):
            p=prediction();p['raw_actions'][0 if first else 3][0]=20.
            with self.assertRaises(Hold) as caught:plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
            self.assertEqual(caught.exception.observed['code'],code)
    def test_bad_dimension_nan_gripper_and_count_rejected(self):
        for kind in ('nan','dimension','gripper','count'):
            p=prediction()
            if kind=='nan':p['raw_actions'][3][0]=float('nan')
            if kind=='dimension':p['raw_actions'][3]=[0.]
            if kind=='gripper':p['raw_actions'][3][5]=101.
            if kind=='count':p['raw_actions'].pop()
            with self.assertRaises(Hold):plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
    def test_boundary_projection_is_recorded(self):
        p=prediction(.1);b=[(-1.,1.)]*5+[(0.,100.)]
        plan=plan_actions(p['observation_state'],p['raw_actions'],b,WorkSegmentLimits(),Q)
        self.assertTrue(plan['boundary_projected_raw_indices'])
        self.assertTrue(all(-1+Q[i]<=r['waypoint'][i]<=1-Q[i]+1e-8 for r in plan['points'] for i in range(5)))
    def test_memory_and_quantum_budgets_rejected(self):
        p=prediction(3.2)
        with self.assertRaises(Hold):plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q,max_points=5)
        for q in (0.,-.1,.5):
            with self.assertRaises(Hold):plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),[q]*5+[.0612])
    def test_cursor_does_not_skip_an_undelivered_point(self):
        p=prediction(.3);plan=plan_actions(p['observation_state'],p['raw_actions'],BOUNDS,WorkSegmentLimits(),Q)
        c=TrajectoryCursor(plan,WorkSegmentLimits(),BOUNDS,Q);c.index=9
        proposal=c.propose(p['observation_state'],p['observation_state'],1/30)
        self.assertFalse(c.acknowledge(proposal,proposal['target']));self.assertEqual(c.index,9)

class MonitorTests(unittest.TestCase):
    def test_stationary_with_persistent_error_is_joint_specific(self):
        m=ProgressMonitor(Q);state=[0.]*5+[10.];goal=[1.5,0.,0.,0.,0.,10.]
        with self.assertRaises(Hold) as caught:
            for i in range(32):m.observe(i/30,state,goal)
        self.assertEqual(caught.exception.observed['code'],'TRACKING_STALLED')
        self.assertEqual([r['joint'] for r in caught.exception.observed['joints']],['shoulder_pan'])
    def test_stationary_at_requested_goal_is_not_a_stall(self):
        m=ProgressMonitor(Q)
        for i in range(90):m.observe(i/30,[0.]*5+[10.],[0.]*5+[10.])
    def test_moving_despite_following_error_is_not_stalled(self):
        m=ProgressMonitor(Q)
        for i in range(90):m.observe(i/30,[i*.1]*5+[10.],[i*.1+1.5]*5+[10.])
    def test_clock_reversal_and_invalid_threshold_rejected(self):
        m=ProgressMonitor(Q);m.observe(1.,[0.]*5+[10.],[0.]*5+[10.])
        with self.assertRaises(Hold):m.observe(.9,[0.]*5+[10.],[0.]*5+[10.])
        with self.assertRaises(Hold):ProgressMonitor(Q,TrackingThresholds(window_seconds=0))

class LegacyDiagnosticTests(unittest.TestCase):
    def test_gap_reports_current_and_sent_target_without_changing_threshold(self):
        with self.assertRaises(Hold) as caught:target_gate([0.,0.,83.,0.,0.,10.],[0.,0.,96.,0.,0.,10.],[0.,0.,94.,0.,0.,10.],BOUNDS,Limits(),1/30)
        self.assertEqual(str(caught.exception),'큰 목표 점프')
        row=caught.exception.observed['joints'][0]
        self.assertEqual(row['joint'],'elbow_flex');self.assertEqual(row['current_gap'],13);self.assertEqual(row['previous_sent_gap'],11)
    def test_previous_target_uses_actual_encoded_wire(self):
        from test_first_trial import Hardware,Clock,HOME,EVIDENCE
        clock=Clock();hw=Hardware(clock);encoded=[]
        def sdk(target):
            wire=[v-.02 if i<5 else v for i,v in enumerate(target)]
            hw.targets.append(target);hw.data['state']=wire[:];encoded.append(wire)
            return dict(wire_normalized=wire)
        hw.send_target=sdk
        a=FirstTrialAdapter(hw,EVIDENCE,HOME,BOUNDS,waiter=immediate,clock=clock,sleep=clock.sleep)
        load_controller(mock_posix=True)(a).run(FirstMotionPlan(False))
        self.assertEqual(a.last_target_context['previous_target'],encoded[-2]);self.assertTrue(hw.closed)

class AdapterTests(unittest.TestCase):
    def build(self):
        hw=SimulatedIO(prediction(.3));home=dict(joint_names=list(JOINTS),state=hw.state[:],tolerance_deg=5.)
        evidence=dict(kind='FIXTURE',**dict.fromkeys(('model_verified','units_verified','mapping_verified','operator_live_confirmed','arm_receipt_verified'),True))
        a=PacedTrialAdapter(hw,evidence,home,BOUNDS,limits=WorkSegmentLimits(),waiter=immediate,clock=hw.clock,sleep=hw.clock.sleep)
        return hw,a,load_controller(mock_posix=True)(a)
    def test_ideal_and_slow_simulated_plants_progress(self):
        for plant in ('ideal','slow'):
            result=run_case(prediction(.3),BOUNDS,plant)
            self.assertNotEqual(result['status'],'HOLD');self.assertGreater(result['final_simulated_state'][0],5)
            self.assertTrue(result['closed']);self.assertEqual(result['motor_register_writes'],0)
    def test_stationary_plant_holds_for_tracking_before_large_gap(self):
        result=run_case(prediction(.3),BOUNDS,'stationary')
        self.assertEqual(result['status'],'HOLD');self.assertEqual(result['error']['observed']['code'],'TRACKING_STALLED')
        self.assertTrue(result['closed']);self.assertLess(result['simulated_commands'],50)
    def test_physical_adapter_cannot_connect(self):
        hw,a,c=self.build();hw.physical=True;a.physical=True;calls=[];hw.connect=lambda:calls.append('connect')
        with self.assertRaises(Hold) as caught:c.run(FirstMotionPlan(True))
        self.assertEqual(caught.exception.observed['code'],'FIELD_VALIDATION_REQUIRED');self.assertEqual(calls,[]);self.assertEqual(hw.commands,[]);self.assertTrue(hw.closed)
    def test_temperature_load_torque_mode_and_status_block_before_send(self):
        for key,value in [('temperatures',55.),('loads',200.),('torque',0),('modes',1),('statuses',1)]:
            hw,a,c=self.build();original=hw.snapshot
            def bad(original=original,key=key,value=value):
                row=original();row[key][JOINTS[0]]=value;return row
            hw.snapshot=bad
            with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
            self.assertEqual(hw.commands,[]);self.assertTrue(hw.closed)
    def test_stale_or_repeated_camera_blocks_before_send(self):
        for kind in ('stale','repeated'):
            hw,a,c=self.build();original=hw.capture_frames
            def bad(original=original,kind=kind):
                frames=original()
                if kind=='stale':frames[CAMERA]['host_received']-=.3
                else:
                    for row in frames.values():row['sha256']='fixed'
                return frames
            CAMERA='end_effector';hw.capture_frames=bad
            with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
            self.assertEqual(hw.commands,[]);self.assertTrue(hw.closed)
    def test_inference_delay_blocks_before_send(self):
        hw,a,c=self.build();hw.prediction['inference_seconds']=2.
        with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
        self.assertEqual(hw.commands,[]);self.assertTrue(hw.closed)
    def test_sdk_wire_violation_preserves_attempt_and_stops(self):
        hw,a,c=self.build();original=hw.send_target
        def bad(target):
            reply=original(target);reply['wire_normalized'][0]+=5;return reply
        hw.send_target=bad
        with self.assertRaises(Hold) as caught:c.run(FirstMotionPlan(False))
        self.assertEqual(caught.exception.observed['code'],'WIRE_TARGET_MISMATCH');self.assertEqual(len(a.attempts),1);self.assertEqual(len(hw.commands),1);self.assertTrue(hw.closed)
    def test_sdk_exception_is_not_retried_and_closes(self):
        hw,a,c=self.build()
        def fail(target):raise OSError('fixture')
        hw.send_target=fail
        with self.assertRaises(OSError):c.run(FirstMotionPlan(False))
        self.assertEqual(len(a.attempts),1);self.assertEqual(a.attempts[0]['status'],'ERROR');self.assertTrue(hw.closed)
    def test_ctrl_c_records_attempt_and_closes(self):
        hw,a,c=self.build()
        def interrupt(target):raise KeyboardInterrupt()
        hw.send_target=interrupt
        with self.assertRaises(KeyboardInterrupt):c.run(FirstMotionPlan(False))
        self.assertEqual(a.attempts[0]['status'],'ERROR');self.assertTrue(hw.closed);self.assertTrue(a.stop.is_set())
    def test_stop_button_after_one_mock_command_sends_no_more(self):
        hw,a,c=self.build();send=hw.send_target
        def stop(target):reply=send(target);a.stop.set();return reply
        hw.send_target=stop
        with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
        self.assertEqual(len(hw.commands),1);self.assertTrue(hw.closed)
    def test_temperature_rise_after_three_commands_holds(self):
        hw,a,c=self.build();snapshot=hw.snapshot
        def temperature():
            row=snapshot()
            if len(hw.commands)>=3:row['temperatures'][JOINTS[0]]=55.
            return row
        hw.snapshot=temperature
        with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
        self.assertEqual(len(hw.commands),3);self.assertTrue(hw.closed)
    def test_start_outside_home_is_rejected(self):
        hw,a,c=self.build();hw.state[0]=6.
        with self.assertRaises(Hold):c.run(FirstMotionPlan(False))
        self.assertEqual(hw.commands,[]);self.assertTrue(hw.closed)

if __name__=='__main__':unittest.main()
