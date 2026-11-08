import unittest
from first_trial_core import Hold
from profile_control_timing import measure
from test_control_timing import FakeIO


class Tests(unittest.TestCase):
    def test_pinned_sha256_values_have_exactly_64_characters(self):
        import inspect,re,reviewed_start_stop
        values=re.findall(r'"([0-9a-f]{64,})"',inspect.getsource(reviewed_start_stop))
        self.assertTrue(values)
        self.assertTrue(all(len(v)==64 for v in values))
    def test_compensation_keeps_reads_and_obtains_requested_mock_period(self):
        hw=FakeIO();rows=measure(hw,clock=hw.clock,sleep=hw.sleep,compensated=True)
        self.assertEqual((hw.snapshots,hw.cameras),(10,2))
        for i in range(1,10):self.assertAlmostEqual(rows[i]["marker_at"]-rows[i-1]["marker_at"],1/30)
        self.assertTrue(all(r["goal_send_seconds"] is None for r in rows))
    def test_over_budget_work_does_not_produce_catchup_bursts(self):
        hw=FakeIO();original=hw.snapshot
        def slow():hw.now+=.04;return original()
        hw.snapshot=slow
        rows=measure(hw,clock=hw.clock,sleep=hw.sleep,compensated=True)
        self.assertTrue(all(rows[i]["marker_at"]-rows[i-1]["marker_at"]>=.05 for i in range(1,10)))
    def test_sleep_stall_refuses_stale_sample(self):
        hw=FakeIO()
        def stalled(n):hw.now+=n+.04
        with self.assertRaises(Hold):measure(hw,clock=hw.clock,sleep=stalled,compensated=True)


if __name__=="__main__":unittest.main()
