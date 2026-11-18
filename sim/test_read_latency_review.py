import unittest
from read_latency_review import analyze


class ReadLatencyTests(unittest.TestCase):
    def test_wrong_id_latency_is_never_counted_as_good(self):
        rows = [dict(event='wire_tx', monotonic_s=10., packet_hex='fffffe0682380201c0'),
                dict(event='wire_rx', monotonic_s=10.17, expected_id=1, received_id=2,
                     communication=0, checksum_valid=True)]
        result = analyze(rows)
        self.assertEqual(result['successful_request_to_reply_s']['n'], 0)
        self.assertEqual(result['all_request_to_reply_s']['n'], 1)
        self.assertAlmostEqual(result['all_request_to_reply_s']['max'], .17)

    def test_missing_clock_or_samples_stays_unknown(self):
        result = analyze([])
        self.assertIsNone(result['goal_gap_s']['p95'])
        self.assertIsNone(result['all_request_to_reply_s']['max'])


if __name__ == '__main__': unittest.main()
