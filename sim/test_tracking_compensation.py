"""Control behavior with a deadband and gravity error, no devices."""
import copy
import math
import unittest

from config_runtime import load
from tracking_compensation import TrackingCompensator


JOINTS = load()['joint_names']
SETTINGS = load()['tracking_compensation']


def gravity_trial(enabled, settings=None):
    settings = copy.deepcopy(SETTINGS if settings is None else settings)
    settings['enabled_axes'] = ['elbow_flex'] if enabled else []
    comp = TrackingCompensator(JOINTS, settings, bounds=[(-100., 100.)]*6)
    position = [0.]*6
    records = []
    dt = 1/60
    for k in range(720):
        reference = [0., 0., min(10., k*dt*2.5), 0., 0., 0.]
        target, evidence = comp.apply(reference, position, k*dt)
        # A 0.6-degree deadband plus 1.8-degree load/gravity equivalent.
        residual = target[2]-1.8-position[2]
        driven_error = math.copysign(max(0., abs(residual)-.6), residual)
        position[2] += driven_error*(1-math.exp(-dt/.16))
        records.append((k*dt, reference[2], target[2], position[2], evidence['offset_deg'][2]))
    tail = records[-120:]
    error = sum(abs(row[1]-row[3]) for row in tail)/len(tail)
    return dict(steady_mae_deg=error, final_reach_ratio=position[2]/10,
                max_offset_deg=max(abs(row[4]) for row in records), final_position_deg=position[2])


class TrackingTests(unittest.TestCase):
    def test_seventy_percent_within_one_second_and_old_new_comparison(self):
        for error in (.5, 2., 4., 10.):
            comp=TrackingCompensator(JOINTS,SETTINGS,bounds=[(-200.,200.)]*6)
            for k in range(61):
                _,meta=comp.apply([0.,0.,error,0.,0.,0.],[0.]*6,k/60)
            self.assertGreaterEqual(meta['offset_deg'][2],min(.7*error,comp.cap))
        old={**SETTINGS,'integral_gain_per_s':.5,'max_accumulation_deg_s':.75,'settle_s':.3}
        self.assertGreater(gravity_trial(True)['final_reach_ratio'],gravity_trial(True,old)['final_reach_ratio'])

    def test_deadband_gravity_tracking_improves(self):
        before, after = gravity_trial(False), gravity_trial(True)
        self.assertLess(after['steady_mae_deg'], before['steady_mae_deg']*.65)
        self.assertGreater(after['final_reach_ratio'], before['final_reach_ratio'])
        self.assertLessEqual(after['max_offset_deg'], 3)

    def test_cap_slew_and_other_axes_unchanged(self):
        comp = TrackingCompensator(JOINTS, SETTINGS, bounds=[(-200., 200.)]*6)
        previous = 0.
        for k in range(1000):
            values, meta = comp.apply([0., 0., 50., 0., 0., 10.], [0.]*6, k/60)
            offset = meta['offset_deg'][2]
            self.assertLessEqual(abs(offset), 3)
            self.assertLessEqual(abs(offset-previous), SETTINGS['max_accumulation_deg_s']/60+1e-9)
            self.assertEqual(values[:2]+values[3:], [0., 0., 0., 0., 10.])
            previous = offset

    def test_range_antiwindup_then_recovery(self):
        comp = TrackingCompensator(JOINTS, SETTINGS, bounds=[(-100., 100.)]*6)
        for k in range(600):
            values, meta = comp.apply([0., 0., 100., 0., 0., 0.], [0.]*6, k/60)
            self.assertEqual(values[2], 100.)
            self.assertEqual(meta['offset_deg'][2], 0.)
        values, _ = comp.apply([0., 0., 0., 0., 0., 0.], [0.]*6, 10.01)
        self.assertEqual(values[2], 0.)

    def test_reversed_error_unwinds_and_paused_loop_does_not_wind_up(self):
        comp = TrackingCompensator(JOINTS, SETTINGS)
        for k in range(300):
            comp.apply([0., 0., 10., 0., 0., 0.], [0.]*6, k/60)
        old = comp.offsets[2]
        comp.apply([0., 0., 10., 0., 0., 0.], [0., 0., 20., 0., 0., 0.], 5.)
        self.assertLess(comp.offsets[2], old)
        old = comp.offsets[2]
        comp.apply([0., 0., 10., 0., 0., 0.], [0.]*6, 20.)
        self.assertLessEqual(comp.offsets[2], old)

    def test_nan_and_gripper_compensation_rejected(self):
        comp = TrackingCompensator(JOINTS, SETTINGS)
        with self.assertRaises(ValueError):
            comp.apply([math.nan]*6, [0.]*6, 0)
        with self.assertRaises(ValueError):
            TrackingCompensator(JOINTS, {**SETTINGS, 'enabled_axes': ['gripper']})


if __name__ == '__main__':
    unittest.main()
