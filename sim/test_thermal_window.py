"""Temporal protection behavior, including high bursts that pass old rereads."""
import unittest

import direct_live as base
from config_runtime import load
from thermal_window import ThermalWindow


class ThermalWindowTests(unittest.TestCase):
    def drive(self, value_for, duration=6):
        window=ThermalWindow(base.JOINTS,load()['temperature'])
        rows=[]
        for k in range(int(duration*50)+1):
            rows.append(window.observe_axis('shoulder_lift',k/50,value_for(k)))
        return rows

    def test_single_and_two_consecutive_81c_bursts_continue(self):
        for length in (1,2):
            rows=self.drive(lambda k:81 if 150<=k<150+length else 34)
            self.assertFalse(any(x['hold'] for x in rows))
            self.assertEqual(rows[-1]['spike_count'],1)
            self.assertTrue(any(x['ended_spike'] and x['ended_spike']['samples']==length for x in rows))

    def test_two_seconds_all_hot_holds_at_boundary(self):
        rows=self.drive(lambda k:55,duration=2)
        self.assertFalse(any(x['hold'] for x in rows[:-1]))
        self.assertTrue(rows[-1]['hold'])
        self.assertEqual(rows[-1]['reason'],'continuous_hot_2s')

    def test_return_to_normal_resets_continuous_hot_streak(self):
        rows=self.drive(lambda k:56 if 20<=k<90 else 34,duration=2)
        self.assertEqual(rows[-1]['continuous_hot_s'],0)
        self.assertEqual(rows[-1]['spike_count'],1)

    def test_slow_actual_temperature_rise_holds(self):
        rows=self.drive(lambda k:34+k*.12,duration=8)
        first=next(x for x in rows if x['hold'])
        self.assertGreaterEqual(first['median_c'],55)
        self.assertTrue(first['window_ready'])

    def test_median_warns_at_50_and_holds_with_intermittent_cool_samples(self):
        warm=self.drive(lambda k:52)
        self.assertTrue(warm[-1]['warning'])
        self.assertFalse(any(x['hold'] for x in warm))
        mixed=self.drive(lambda k:34 if k%3==0 else 56)
        self.assertTrue(mixed[-1]['hold'])
        self.assertEqual(mixed[-1]['reason'],'median_2s')
        self.assertLess(mixed[-1]['continuous_hot_s'],.1)

    def test_missing_interval_cannot_count_as_continuous_hot(self):
        window=ThermalWindow(base.JOINTS,load()['temperature'])
        window.observe_axis('shoulder_lift',0,80)
        row=window.observe_axis('shoulder_lift',3,80)
        self.assertTrue(row['sample_gap_reset'])
        self.assertFalse(row['window_ready'])
        self.assertFalse(row['hold'])

    def test_invalid_or_incomplete_decision_is_not_fail_open(self):
        sample=dict(temperatures=dict.fromkeys(base.JOINTS,34),statuses=dict.fromkeys(base.JOINTS,0),
                    temperature_protection={})
        with self.assertRaises(base.Stop):base.feedback_gate(sample)

    def test_status_error_still_stops_during_a_filtered_spike(self):
        window=ThermalWindow(base.JOINTS,load()['temperature'])
        decisions={j:window.observe_axis(j,0,81) for j in base.JOINTS}
        sample=dict(temperatures=dict.fromkeys(base.JOINTS,81),statuses=dict.fromkeys(base.JOINTS,0),
                    temperature_protection=decisions)
        base.feedback_gate(sample)
        sample['statuses']['shoulder_lift']=1
        with self.assertRaises(base.Stop):base.feedback_gate(sample)

    def test_samples_must_have_new_monotonic_times(self):
        window=ThermalWindow(base.JOINTS,load()['temperature'])
        window.observe_axis('shoulder_lift',1,34)
        with self.assertRaises(ValueError):window.observe_axis('shoulder_lift',1,34)


if __name__=='__main__':unittest.main()
