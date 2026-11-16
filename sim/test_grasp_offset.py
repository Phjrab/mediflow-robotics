"""Only a physically verified grasp-window offset may change shoulder_pan."""
import unittest
from config_runtime import load
from grasp_offset import GraspOffset


class GraspOffsetTests(unittest.TestCase):
    def setup_guard(self,**changes):
        settings={**load()['lateral_correction'],'enabled':True,'direction_verified':True,
                  'geometry_verified':True,'offset_deg':4*360/4095,**changes}
        return GraspOffset(settings,height=lambda state:state[0])

    def test_default_physical_bias_disabled(self):
        guard=GraspOffset();target=[3,-23,12,66,-5,45]
        actual,why=guard.apply(target,[130]*6,0,[],9.9)
        self.assertEqual(actual,target);self.assertEqual(why['applied_deg'],0)

    def test_unverified_direction_or_tcp_blocks_activation(self):
        for flags in ({'direction_verified':False},{'geometry_verified':False}):
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


if __name__=='__main__':unittest.main()
