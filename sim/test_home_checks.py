"""Position margins, integer convergence and retry regression tests; no hardware."""
import json
import unittest
from config_runtime import load
from start_position_rules import HomeConvergence, assess_history, home_targets, position_check
from startup_simulation import SimulatedArm
import direct_live as base
import live_start
from stream_live import start_policy_session


class FrozenArm(SimulatedArm):
    def feedback(self):
        if not hasattr(self,'fixed'):self.fixed=dict(self.present)
        sample=super().feedback()
        self.present=dict(self.fixed)
        raw={j:round(v) for j,v in self.fixed.items()}
        sample.update(present_raw=raw,state=self.normalize(raw))
        return sample


class HomeChecks(unittest.TestCase):
    def arm(self, ticks=None, off=False):
        c=load();events=[]
        io=FrozenArm(c['home']['state'],{},events.append,'all_off' if off else 'camera_dry')
        io.present=home_targets(c);io.present.update(ticks or {});io.target=dict(io.present)
        return io,events

    def test_3084_3085_3087_start_without_home_goal(self):
        for ticks in (3084,3085,3087):
            with self.subTest(ticks=ticks):
                io,events=self.arm({'elbow_flex':ticks})
                result=live_start.prepare(io,io.home,events.append)
                self.assertEqual(io.packet_count,0)
                self.assertEqual(result['auto_recovered'],[])
                self.assertGreaterEqual(io.clock(),.5)
                self.assertEqual(any(r['event']=='startup_position_warning' for r in events),ticks>3084)

    def test_3088_blocks_before_any_write(self):
        io,events=self.arm({'elbow_flex':3088},off=True)
        with self.assertRaisesRegex(base.Stop,'elbow_flex.*3088'):
            live_start.prepare(io,io.home,events.append)
        self.assertEqual(io.start_writes,[]);self.assertEqual(io.packet_count,0)

    def test_four_ticks_pass_but_seven_retry_three_times(self):
        io,events=self.arm({'wrist_roll':home_targets()['wrist_roll']+4})
        live_start.prepare(io,io.home,events.append)
        self.assertEqual(io.packet_count,0)
        io,events=self.arm({'wrist_roll':home_targets()['wrist_roll']+7})
        with self.assertRaisesRegex(base.Stop,'홈 이동 후 위치 확인 실패:') as error:
            live_start.prepare(io,io.home,events.append)
        detail=json.loads(str(error.exception).split(': ',1)[1])
        self.assertEqual(detail['attempts'],3)
        self.assertEqual(detail['joints']['wrist_roll']['residual_ticks'],7)
        self.assertEqual(sum(r['event']=='startup_home_attempt' for r in events),3)

    def test_half_second_and_fresh_samples_required(self):
        raw=home_targets();window=HomeConvergence()
        for t in (0,.1,.2,.3,.4):self.assertFalse(window.observe(raw,t)['ready'])
        self.assertTrue(window.observe(raw,.5)['ready'])
        self.assertFalse(window.observe(raw,1.)['ready'])
        self.assertFalse(window.observe(raw,1.)['ready'])

    def test_outside_residual_resets_stability(self):
        raw=home_targets();window=HomeConvergence()
        for t in (0,.2,.4):window.observe(raw,t)
        bad={**raw,'wrist_roll':raw['wrist_roll']+7}
        self.assertFalse(window.observe(bad,.5)['ready'])
        self.assertFalse(window.observe(raw,.6)['ready'])

    def test_gripper_two_points_uses_calibration_tick_scale(self):
        raw=home_targets()
        self.assertTrue(position_check({**raw,'gripper':raw['gripper']+32})['home_converged'])
        self.assertFalse(position_check({**raw,'gripper':raw['gripper']+33})['home_converged'])

    def test_command_bounds_do_not_expand_when_off_axis_is_above_upper(self):
        io,events=self.arm({'elbow_flex':3087},off=True)
        live_start.prepare(io,io.home,events.append)
        for register,values in io.start_writes:
            if register=='Goal_Position':
                self.assertLessEqual(values['elbow_flex'],3084)
        errors=[r['goal_error_ticks']['elbow_flex'] for r in events if r['event']=='startup_torque_readback']
        self.assertTrue(errors);self.assertLessEqual(max(errors),3)
        self.assertEqual(io.arm_bounds['elbow_flex'],(880,3084))

    def test_upper_margin_does_not_change_lower_limit(self):
        c=load();raw=home_targets()
        for j,(lo,hi) in c['limits'].items():
            self.assertTrue(position_check({**raw,j:hi+3})['safe'])
            self.assertFalse(position_check({**raw,j:hi+4})['safe'])
            self.assertFalse(position_check({**raw,j:lo-1})['safe'])

    def test_website_and_controller_share_history_decision(self):
        raw={**home_targets(),'elbow_flex':3087};window=HomeConvergence();samples=[]
        for t in (0,.2,.4,.6):
            samples.append(dict(time_s=t,present_raw=raw))
            self.assertEqual(assess_history(samples),window.observe(raw,t))

    def test_accepted_margin_enters_same_policy_loop(self):
        io,events=self.arm({'elbow_flex':3087})
        io.pool_factory=lambda **kw:__import__('startup_simulation').VirtualExecutor(io.clock,False)
        def policy(frames,state,origin,delay):return [io.home[:] for _ in range(50)],.02
        result=start_policy_session(io,policy,io.home,events.append,.2,.33)
        self.assertEqual(result['status'],'RUN_COMPLETE');self.assertGreater(result['steps'],0)
        self.assertTrue(all(r['wire_raw']['elbow_flex']<=3084 for r in events if r['event']=='goal_transmitted'))


if __name__=='__main__':unittest.main()
