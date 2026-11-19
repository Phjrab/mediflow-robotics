"""Independent raw FIFO/safety regressions. No serial, cameras, or model loads."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import direct_live as base
from config_runtime import load
from raw_live import AbsoluteDeadline, FifoChunkCursor, JumpGuard, load_reference


JOINTS = tuple(load()['joint_names'])
HOME = list(load()['home']['state'])


def actions(first, count):
    return [[float(first+i)]*6 for i in range(count)]


class RawPrimitivesTests(unittest.TestCase):
    def test_absolute_deadline_skips_time_slots_without_catchup_loop(self):
        clock = AbsoluteDeadline(30, 100.)
        first = clock.begin(100.)
        self.assertAlmostEqual(first['deadline'], 100.)
        self.assertEqual(first['skipped'], 0)
        delayed = clock.begin(100.12)
        self.assertEqual(delayed['skipped'], 2)
        self.assertAlmostEqual(delayed['deadline'], 100.1)
        self.assertGreater(clock.next, 100.12)
        self.assertLessEqual(clock.next-100.12, 1/30+1e-9)
        self.assertEqual(clock.skipped, 2)

    def test_fifo_keeps_prefix_and_exact_values_at_chunk_boundary(self):
        cursor = FifoChunkCursor(n_action_steps=3)
        cursor.prime(actions(0,3), observed_at=1.)
        cursor.stage(actions(3,3), observed_at=1.5)
        got = [cursor.pop(4.) for _ in range(6)]
        self.assertEqual([row[0] for row in got], list(range(6)))
        self.assertEqual([row[1] for row in got], actions(0,6))
        self.assertEqual([row[2] for row in got], [3.]*3+[2.5]*3)
        self.assertEqual(cursor.remaining, 0)
        self.assertEqual(cursor.index, 6)

    def test_fifo_has_only_one_next_chunk_and_does_not_overwrite_pending(self):
        cursor = FifoChunkCursor(n_action_steps=3)
        cursor.prime(actions(0,3), observed_at=0.)
        cursor.stage(actions(3,3), observed_at=.1)
        with self.assertRaises((RuntimeError, ValueError, base.Stop)):
            cursor.stage(actions(99,3), observed_at=.2)
        self.assertEqual([cursor.pop(1.)[1] for _ in range(6)], actions(0,6))

    def test_jump_guard_uses_last_successful_command_not_actual_servo_lag(self):
        guard = JumpGuard(dict.fromkeys(JOINTS, 1.), [0.]*6, [0.]*6)
        for number in range(1,11):
            model = [number*.5]*6
            guard.check(model, model)
            guard.commit(model, model)
        with self.assertRaises(base.Stop):
            guard.check([7.]*6, [7.]*6)

    def test_jump_guard_checks_first_home_and_postprocessing_wire_separately(self):
        guard = JumpGuard(dict.fromkeys(JOINTS, 1.), [0.]*6, [0.]*6)
        model = [0.]*6;model[2] = 1.1
        with self.assertRaisesRegex(base.Stop, 'elbow_flex'):
            guard.check(model, [0.]*6)
        wire = [0.]*6;wire[4] = -1.1
        with self.assertRaisesRegex(base.Stop, 'wrist_roll'):
            guard.check([0.]*6, wire)
        # Failed validation must never advance the guard's reference.
        guard.check([.5]*6,[.5]*6)

    def test_nonfinite_target_never_reaches_wire(self):
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.subTest(value=value):
                guard = JumpGuard(dict.fromkeys(JOINTS, 1.), [0.]*6, [0.]*6)
                row=[0.]*6;row[5]=value
                with self.assertRaises((base.Stop,ValueError)):
                    guard.check(row,[0.]*6)


class RawReferenceTests(unittest.TestCase):
    def fixture(self, directory):
        template = Path(__file__).resolve().parents[1]/'analysis/raw_action_reference.json'
        reference=json.loads(template.read_text(encoding='utf-8'))
        for index,row in enumerate(reference['source_metadata']):
            target=Path(directory)/f'info_{index}.json'
            metadata=dict(fps=30,features={'action':{'names':[j+'.pos' for j in JOINTS]}})
            target.write_text(json.dumps(metadata),encoding='utf-8')
            row.update(path=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        path=Path(directory)/'reference.json'
        path.write_text(json.dumps(reference),encoding='utf-8')
        return path,reference

    def test_verified_metadata_fps_is_used_and_missing_metadata_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path,reference=self.fixture(directory)
            self.assertEqual(load_reference(path)['fps'],30)
            Path(reference['source_metadata'][2]['path']).unlink()
            with self.assertRaises((base.Stop,ValueError,FileNotFoundError)):
                load_reference(path)

    def test_metadata_hash_change_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path,reference=self.fixture(directory)
            Path(reference['source_metadata'][0]['path']).write_text('{"fps": 30, "changed": true}',encoding='utf-8')
            with self.assertRaises((base.Stop,ValueError)):
                load_reference(path)

    def test_metadata_fps_mismatch_even_with_updated_hash_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path,reference=self.fixture(directory)
            row=reference['source_metadata'][0]
            source=Path(row['path']);metadata=json.loads(source.read_text(encoding='utf-8'))
            metadata['fps']=15;source.write_text(json.dumps(metadata),encoding='utf-8')
            row.update(sha256=hashlib.sha256(source.read_bytes()).hexdigest(),fps=15)
            path.write_text(json.dumps(reference),encoding='utf-8')
            with self.assertRaises((base.Stop,ValueError)):
                load_reference(path)


class RecordingPolicy:
    def __init__(self, trajectory, n_action_steps=50, inference_seconds=.001):
        self.trajectory=trajectory
        self.n_action_steps=n_action_steps
        self.inference_seconds=inference_seconds
        self.calls=[]

    def __call__(self, frames, state, origin=0, delay=0):
        start=len(self.calls)*self.n_action_steps
        self.calls.append(dict(state=list(state),origin=origin,delay=delay))
        # The checkpoint chunk_size is 50; n_action_steps may consume fewer.
        rows=[list(self.trajectory(start+i)) for i in range(max(50,self.n_action_steps))]
        return rows,self.inference_seconds


class RawStreamTests(unittest.TestCase):
    def run_stream(self, trajectory=None, *, duration=.3, case='camera_dry',
                   n_action_steps=50, read_delay=0, limits=None, arm_setup=None,
                   inference_seconds=.001,policy_factory=None):
        from raw_live import raw_stream
        from startup_simulation import SimulatedArm
        rows=[]
        arm=SimulatedArm(HOME,{},rows.append,case)
        arm.policy_active=True
        sent=[]
        send=arm.send
        def capture(raw):
            sent.append(dict(at=arm.clock(),raw=dict(raw)))
            return send(raw)
        arm.send=capture
        if arm_setup is not None:
            arm_setup(arm)
        if read_delay:
            checked=arm.checked
            def delayed():
                arm.pause(read_delay)
                return checked()
            arm.checked=delayed
        if trajectory is None:
            def trajectory(index):
                target=list(HOME);target[2]-=.1*index
                return target
        policy=(RecordingPolicy(trajectory,n_action_steps,inference_seconds)
                if policy_factory is None else policy_factory(arm))
        reference=dict(fps=30,raw_guard_1_5=limits or dict.fromkeys(JOINTS,1.))
        with contextlib.redirect_stdout(io.StringIO()):
            result=raw_stream(arm,policy,rows.append,duration,requested_speed=.33,
                              reference=reference)
        return result,rows,sent,arm,policy,trajectory

    def test_gripper_wait_releases_during_fifo_gap_without_advancing_arm(self):
        for outcome in ('arrived', 'timeout'):
            with self.subTest(outcome=outcome):
                def setup(arm):
                    checked = arm.checked
                    def feedback():
                        sample = checked()
                        state = list(HOME)
                        state[-1] = 25
                        if outcome == 'arrived' and arm.clock() >= .7:
                            state[0] += 6
                        pose = arm.plan(state)
                        sample.update(state=pose['wire_normalized'], present_raw=pose['wire_raw'])
                        return sample
                    arm.checked = feedback
                def policy_for(arm):
                    def policy(frames, state, origin=0, delay=0):
                        target = list(HOME)
                        target[0] += 6
                        target[-1] = 15
                        return [target[:] for _ in range(50)], .001 if origin == 0 else 3.
                    policy.n_action_steps = 1
                    return policy
                result, rows, sent, _, _, _ = self.run_stream(duration=1.9, arm_setup=setup,
                    policy_factory=policy_for, limits=dict.fromkeys(JOINTS, 30.))
                self.assertEqual(result['status'], 'RUN_COMPLETE', result['reason'])
                self.assertEqual(result['steps'], 1)
                # This fixture holds the actual gripper at 25 points after the
                # release. A sufficiently long blocked close invokes GripRelief.
                safety = result['gripper_safety_events']
                expected = ['gripper_close_wait_release']
                if outcome == 'arrived':
                    expected.append('closing_relief')
                self.assertEqual([e['reason'] for e in safety], expected)
                self.assertEqual(len(sent), 1+len(expected))
                hold, release = [r['raw'] for r in sent[:2]]
                self.assertLess(release['gripper'], hold['gripper'])
                for packet in sent[1:]:
                    self.assertTrue(all(hold[j] == packet['raw'][j] for j in JOINTS[:-1]))
                if outcome == 'arrived':
                    self.assertGreater(sent[-1]['raw']['gripper'], release['gripper'])
                event = safety[0]
                self.assertFalse(event['policy_index_advanced'])
                self.assertEqual(event['reason'], 'gripper_close_wait_release')
                wait = result['gripper_close_wait']
                self.assertEqual(wait['outcomes'][outcome], 1)
                if outcome == 'timeout':
                    self.assertGreaterEqual(wait['max_wait_s'], 1.5)
                    self.assertLessEqual(wait['max_wait_s'], 1.5+1/30)
                goal, = [r for r in rows if r['event'] == 'goal_transmitted']
                self.assertEqual(goal['wire_raw']['gripper'], goal['feedback']['present_raw']['gripper'])
                self.assertEqual(goal['model'][-1], 15)
                self.assertNotEqual(goal['wire_raw']['shoulder_pan'], goal['feedback']['present_raw']['shoulder_pan'])

    def test_original_actions_are_sent_once_in_order_without_interpolation(self):
        result,rows,sent,arm,policy,trajectory=self.run_stream()
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertGreater(len(sent),5)
        for index,row in enumerate(sent):
            self.assertEqual(row['raw'],arm.plan(trajectory(index))['wire_raw'])
        self.assertGreaterEqual(min(b['at']-a['at'] for a,b in zip(sent,sent[1:])),1/30-.001)
        timing=[row for row in rows if row.get('event')=='loop_timing']
        self.assertEqual(len(sent),len(timing))
        for row in timing[1:]:
            self.assertGreaterEqual(row['loop_period_ms'],1000/30-.001)
        self.assertLessEqual(len(sent),10)

    def test_new_chunk_keeps_its_first_action_and_jump_guard_checks_boundary(self):
        def trajectory(index):
            row=list(HOME);row[2]-=.1*index
            if index>=3:row[2]-=4
            return row
        result,rows,sent,arm,policy,_=self.run_stream(trajectory,n_action_steps=3,duration=.5)
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('elbow_flex',result['reason'])
        self.assertEqual(len(sent),3)

    def test_delayed_reads_skip_deadlines_but_never_skip_fifo_actions_or_burst(self):
        result,rows,sent,arm,policy,trajectory=self.run_stream(duration=.8,read_delay=.09)
        self.assertGreaterEqual(len(sent),3)
        for index,row in enumerate(sent):
            self.assertEqual(row['raw'],arm.plan(trajectory(index))['wire_raw'])
        self.assertGreaterEqual(min(b['at']-a['at'] for a,b in zip(sent,sent[1:])),.09)
        self.assertGreater(result['skipped_loops'],0)
        self.assertEqual(result['skipped_policy_actions'],0)

    def test_long_read_followed_by_fast_reads_never_catches_up_in_a_burst(self):
        def setup(arm):
            checked=arm.checked;delayed=False
            def variable_read():
                nonlocal delayed
                if arm.packet_count==1 and not delayed:
                    delayed=True;arm.pause(.15)
                return checked()
            arm.checked=variable_read
        result,rows,sent,arm,_,trajectory=self.run_stream(duration=.7,arm_setup=setup)
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertGreater(result['skipped_loops'],0)
        self.assertGreaterEqual(min(b['at']-a['at'] for a,b in zip(sent,sent[1:])),1/30-1e-8)
        for index,row in enumerate(sent):
            self.assertEqual(row['raw'],arm.plan(trajectory(index))['wire_raw'])

    def test_slow_inference_waits_without_repeating_or_dropping_original_actions(self):
        result,rows,sent,arm,_,trajectory=self.run_stream(duration=1.,n_action_steps=3,inference_seconds=.2)
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertGreater(result['chunk_wait_s']['sum'],0)
        self.assertGreater(len(sent),3)
        for index,row in enumerate(sent):
            self.assertEqual(row['raw'],arm.plan(trajectory(index))['wire_raw'])

    def boundary_policy(self, arm, inference_seconds=1.4):
        class StatePolicy:
            n_action_steps=50
            def __init__(self):self.calls=[]
            def __call__(self,frames,state,origin=0,delay=0):
                self.calls.append(dict(sent_count=arm.packet_count,state=list(state),
                                       frames=copy.deepcopy(frames),at=arm.clock()))
                chunk=[]
                for index in range(50):
                    row=list(state);row[1]+=.6*index;chunk.append(row)
                return chunk,inference_seconds
        return StatePolicy()

    def training_jump_limits(self):
        path=Path(__file__).resolve().parents[1]/'analysis/raw_action_reference.json'
        return load_reference(path,verify_metadata=False)['raw_guard_1_5']

    def test_state_conditioned_chunks_observe_after_previous_chunk_finishes(self):
        def setup(arm):
            arm.fresh_frames=lambda:{'test_camera':{'frame_packet_count':arm.packet_count,'timestamp':arm.clock()}}
        result,rows,sent,arm,policy,_=self.run_stream(duration=6.,arm_setup=setup,
                                                   policy_factory=self.boundary_policy,
                                                   limits=self.training_jump_limits())
        self.assertEqual(result['status'],'RUN_COMPLETE',result.get('reason'))
        self.assertGreater(len(sent),50)
        self.assertGreaterEqual(len(policy.calls),2)
        for index,call in enumerate(policy.calls):
            self.assertEqual(call['sent_count'],index*50)
            self.assertEqual(call['frames']['test_camera']['frame_packet_count'],index*50)
        self.assertGreater(policy.calls[1]['state'][1]-policy.calls[0]['state'][1],25.)
        # Every model action remains byte-for-value unchanged through scheduling.
        goals=[row for row in rows if row.get('event')=='goal_transmitted']
        self.assertEqual([row['action_index'] for row in goals],list(range(len(sent))))
        for index,row in enumerate(goals):
            expected=list(policy.calls[index//50]['state']);expected[1]+=.6*(index%50)
            self.assertEqual(row['model'],expected)
        requests=[row for row in rows if row.get('event')=='inference_requested']
        self.assertTrue(requests)
        self.assertTrue(all(row['remaining_actions']==0 for row in requests))

    def test_recorded_27_degree_boundary_jump_still_holds_unchanged(self):
        limits=self.training_jump_limits()
        self.assertAlmostEqual(limits['shoulder_lift'],21.62636947631836)
        previous=list(HOME);previous[1]=-72.00016784667969
        incoming=list(previous);incoming[1]=-99.30259704589844
        guard=JumpGuard(limits,previous,previous)
        with self.assertRaisesRegex(base.Stop,'shoulder_lift'):
            guard.check(incoming,incoming)

    def test_boundary_inference_wait_keeps_safety_and_ctrl_c_checks_active(self):
        for interruption in ('temperature','ctrl_c'):
            with self.subTest(interruption=interruption):
                calls=[]
                def setup(arm):
                    checked=arm.checked;first_wait=None
                    def interrupt():
                        nonlocal first_wait
                        if arm.packet_count>=50:
                            if first_wait is None:first_wait=arm.clock()
                            calls.append(arm.clock())
                            if arm.clock()-first_wait>.15:
                                if interruption=='ctrl_c':raise KeyboardInterrupt
                                raise base.Stop('fixture temperature HOLD during boundary inference')
                        return checked()
                    arm.checked=interrupt
                result,rows,sent,arm,policy,_=self.run_stream(duration=6.,arm_setup=setup,
                                                           policy_factory=self.boundary_policy,
                                                           limits=self.training_jump_limits())
                self.assertEqual(result['status'],'STOPPED' if interruption=='ctrl_c' else 'HOLD')
                self.assertEqual(len(sent),50)
                self.assertGreaterEqual(len(calls),4)

    def test_duration_expires_during_boundary_inference_without_extra_targets(self):
        result,rows,sent,arm,policy,_=self.run_stream(duration=2.1,
                                                   policy_factory=self.boundary_policy,
                                                   limits=self.training_jump_limits())
        self.assertEqual(result['status'],'RUN_COMPLETE',result.get('reason'))
        self.assertEqual(len(sent),50)
        self.assertGreater(result['chunk_wait_s']['sum'],.2)
        self.assertLess(result['elapsed_s'],2.2)
        self.assertEqual(result['skipped_policy_actions'],0)

    def run_blocked_gripper_wait(self, opening):
        def setup(arm):
            send=arm.send;feedback=arm.feedback
            def send_and_block(raw):
                result=send(raw)
                if arm.packet_count==50:arm.gripper_blocked=True
                return result
            def loaded():
                sample=feedback()
                if arm.gripper_blocked and opening:sample['loads']['gripper']=-450
                return sample
            arm.send=send_and_block;arm.feedback=loaded
        def factory(arm):
            class GripperPolicy:
                n_action_steps=50
                def __call__(self,frames,state,origin=0,delay=0):
                    chunk=[HOME[:-1]+[20.] for _ in range(50)]
                    chunk[-1][-1]=40. if opening else 0.
                    return chunk,1.4
            return GripperPolicy()
        return self.run_stream(duration=2.7,arm_setup=setup,policy_factory=factory,
                               limits=self.training_jump_limits())

    def test_opening_load_guard_can_relieve_only_gripper_during_inference_wait(self):
        result,rows,sent,arm,_,_=self.run_blocked_gripper_wait(opening=True)
        self.assertEqual(result['status'],'RUN_COMPLETE',result.get('reason'))
        goals=[row for row in rows if row.get('event')=='goal_transmitted']
        relief=[row for row in rows if row.get('event')=='gripper_safety_goal']
        self.assertEqual(result['steps'],50)
        self.assertEqual(len(goals),50)
        self.assertEqual(len(sent),51)
        self.assertEqual(result['gripper_safety_goal_packets'],1)
        self.assertEqual(len(relief),1)
        self.assertGreaterEqual(sent[-1]['at']-sent[-2]['at'],.3)
        self.assertLess(sent[-1]['raw']['gripper'],sent[-2]['raw']['gripper'])
        for joint in JOINTS[:-1]:
            self.assertEqual(sent[-1]['raw'][joint],sent[-2]['raw'][joint])
        self.assertEqual(arm.start_writes,[])
        self.assertEqual([row['action_index'] for row in goals],list(range(50)))

    def test_closing_relief_timer_advances_and_never_retightens_while_waiting(self):
        result,rows,sent,arm,_,_=self.run_blocked_gripper_wait(opening=False)
        self.assertEqual(result['status'],'RUN_COMPLETE',result.get('reason'))
        goals=[row for row in rows if row.get('event')=='goal_transmitted']
        self.assertEqual(result['steps'],50)
        self.assertEqual(len(goals),50)
        self.assertEqual(len(sent),51)
        self.assertEqual(result['gripper_safety_goal_packets'],1)
        self.assertGreaterEqual(sent[-1]['at']-sent[-2]['at'],.5-1e-8)
        self.assertGreater(sent[-1]['raw']['gripper'],sent[-2]['raw']['gripper'])
        for joint in JOINTS[:-1]:
            self.assertEqual(sent[-1]['raw'][joint],sent[-2]['raw'][joint])
        self.assertEqual(arm.start_writes,[])

    def test_first_policy_jump_and_nan_are_stopped_before_any_goal(self):
        for value in (HOME[2]+3,float('nan')):
            with self.subTest(value=value):
                def trajectory(index):
                    row=list(HOME);row[2]=value
                    return row
                result,rows,sent,_,_,_=self.run_stream(trajectory)
                self.assertEqual(result['status'],'HOLD')
                self.assertEqual(sent,[])

    def test_model_jump_cannot_hide_behind_safe_gripper_clipping(self):
        def trajectory(index):
            return HOME[:-1]+[61.]
        # Model delta >50, but clipping makes the wire delta <50.
        result,rows,sent,_,_,_=self.run_stream(trajectory,limits=dict.fromkeys(JOINTS,50.))
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('gripper',result['reason'])
        self.assertIn('model',result['reason'])
        self.assertEqual(sent,[])

    def test_gripper_and_elbow_physical_caps_still_apply(self):
        def trajectory(index):
            row=list(HOME);row[2]=110;row[5]=61
            return row
        result,rows,sent,arm,_,_=self.run_stream(trajectory,limits=dict.fromkeys(JOINTS,100.))
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertTrue(sent)
        for row in sent:
            self.assertLessEqual(row['raw']['elbow_flex'],3084)
            self.assertLessEqual(arm.normalize(row['raw'])[-1],48.5)

    def test_ctrl_c_keeps_hold_behavior_and_stops_policy_targets(self):
        result,rows,sent,_,_,_=self.run_stream(duration=4,case='ctrl_c')
        self.assertEqual(result['status'],'STOPPED')
        self.assertIn('Ctrl+C',result['reason'])
        self.assertEqual(len(sent),20)

    def test_sustained_temperature_still_stops_after_two_second_window(self):
        result,rows,sent,arm,_,_=self.run_stream(duration=5,case='overheat')
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('온도',result['reason'])
        self.assertGreater(len(sent),20)
        self.assertLess(len(sent),150)
        self.assertGreaterEqual(arm.clock(),2.)

    def test_nonzero_status_is_still_guarded(self):
        def setup(arm):
            feedback=arm.feedback
            def faulty():
                sample=feedback()
                if arm.packet_count>=5:sample['statuses']['wrist_roll']=4
                return sample
            arm.feedback=faulty
        result,rows,sent,_,_,_=self.run_stream(duration=1.,arm_setup=setup)
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('wrist_roll',result['reason'])
        self.assertEqual(len(sent),5)

    def test_usb_disconnect_does_not_confirm_a_hold_it_cannot_send(self):
        result,rows,sent,_,_,_=self.run_stream(duration=4,case='usb_disconnect')
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('USB',result['reason'])
        self.assertEqual(len(sent),20)
        self.assertNotIn('HOLD_CONFIRMED',json.dumps(result['hold']))

    def test_plus_six_tick_offset_is_still_grasp_only_and_pan_only(self):
        pose=[3.5,-23.,13.,67.,-5.,45.]
        def trajectory(index):
            return pose[:-1]+[45. if index<20 else 30.]
        result,rows,sent,arm,_,_=self.run_stream(trajectory,duration=2.,limits=dict.fromkeys(JOINTS,200.))
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertGreater(result['lateral_correction']['applied_steps'],0)
        expected=arm.plan(pose)['wire_raw']
        self.assertEqual(max(row['raw']['shoulder_pan']-expected['shoulder_pan'] for row in sent),6)
        for row in sent:
            for joint in JOINTS[1:5]:
                self.assertEqual(row['raw'][joint],expected[joint])

    def test_recorder_receives_each_raw_goal_and_camera_capture(self):
        captured=[];goals=[];closed=[]
        class Recorder:
            def __init__(self,*args,**kwargs):pass
            def due(self,now):return True
            def capture(self,*args,**kwargs):captured.append((args,kwargs))
            def goal(self,target,row,**kwargs):goals.append(row)
            def close(self,**kwargs):
                closed.append(kwargs)
                return dict(event_count=0,events=[])
        def setup(arm):
            arm.out=Path('never_written_by_stub')
            arm.camera_frames=lambda:{}
        with patch('raw_live.GraspRecorder',Recorder):
            result,rows,sent,_,_,_=self.run_stream(arm_setup=setup)
        self.assertEqual(result['status'],'RUN_COMPLETE')
        self.assertTrue(captured)
        self.assertEqual(len(goals),len(sent))
        self.assertEqual([row['action_index'] for row in goals],list(range(len(sent))))
        self.assertEqual(len(closed),1)


class RawEntryTests(unittest.TestCase):
    def run_entry(self,args):
        import entry
        with tempfile.TemporaryDirectory() as receipt, patch.object(sys,'argv',['entry.py']+args), \
             patch('stream_live.live',return_value=11) as legacy, \
             patch('raw_live.live',return_value=22) as raw, \
             patch('stream_live.DEFAULT_SPEED'), patch('live_start.ACTIVE_SPEED',create=True), \
             patch.dict('os.environ',{'SO101_RUN_RECEIPT':receipt},clear=False), \
             contextlib.redirect_stdout(io.StringIO()):
            result=entry.main()
            return result,legacy,raw

    def test_live_defaults_to_legacy_unchanged(self):
        result,legacy,raw=self.run_entry(['--live','--speed','.33','--duration','180'])
        self.assertEqual(result,11)
        legacy.assert_called_once_with(180.,supported_home=False,speed=.33,
                                      web_corrections=False,taught_forward=None)
        raw.assert_not_called()

    def test_raw_is_only_used_when_explicitly_selected(self):
        result,legacy,raw=self.run_entry(['--live','--mode','raw','--speed','.33','--duration','180'])
        self.assertEqual(result,22)
        raw.assert_called_once_with(180.,supported_home=False,speed=.33)
        legacy.assert_not_called()

    def test_raw_web_corrections_are_rejected_without_dispatch(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as stopped:
            self.run_entry(['--live','--mode','raw','--web-corrections'])
        self.assertNotEqual(stopped.exception.code,0)

    def test_raw_simulation_dispatch_does_not_test_the_legacy_scheduler(self):
        import entry
        with patch.object(sys,'argv',['entry.py','--simulate','--mode','raw']), \
             patch('raw_live.run_simulations',return_value=31) as raw, \
             patch('startup_simulation.run_simulations',return_value=41) as legacy, \
             patch('stream_live.DEFAULT_SPEED'),patch('live_start.ACTIVE_SPEED',create=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(),31)
        raw.assert_called_once_with(180,.33)
        legacy.assert_not_called()

if __name__ == '__main__': unittest.main()
