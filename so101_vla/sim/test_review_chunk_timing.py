import json
from pathlib import Path
import tempfile
import unittest

from review_chunk_timing import analyze


class ChunkLogReviewTests(unittest.TestCase):
    def test_delivery_segments_count_phase_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            rows=[]
            def wire(t):
                rows.append(dict(event='wire_tx',monotonic_s=100+t,unix=1000+t,packet_hex='ff'))
            def inference(t,origin):
                wire(t)
                rows.append(dict(event='inference',origin=origin,seconds=1.4,skipped_actions=14 if origin else 0,
                                 overlap=5,discarded=False))
            def goal(t,phase,value):
                wire(t)
                rows.append(dict(event='goal_transmitted',time_s=t,model_phase=phase,model=[value]*6,
                                 wire_normalized=[value]*6,feedback=dict(state=[value/2]*6)))
            inference(1.,0);goal(1.1,0,0);goal(3.,19,4)
            inference(4.,30);goal(4.1,30,5);goal(6.,49,6)
            raw=''.join(json.dumps(row)+'\n' for row in rows).encode()
            (folder/'steps.jsonl').write_bytes(raw)
            report=analyze(folder,5.)
            self.assertEqual(report['log']['bytes'],len(raw))
            self.assertEqual(report['log']['image_payload_value_bytes'],0)
            self.assertGreater(report['log']['whitespace_saving_bytes'],0)
            self.assertEqual(report['first60']['wire_commands'],3)
            self.assertEqual(report['first60']['inference_count'],2)
            self.assertAlmostEqual(report['first60']['delivery_intervals_s']['mean'],3.)
            self.assertEqual(report['first60']['request_origin_advance_actions']['mean'],30)
            self.assertEqual(report['first60']['chunks'][0]['consumed_phase_span'],19)
            self.assertEqual(report['first60']['chunks'][0]['applied_model_trajectory']['elbow_flex']['span'],4)
            self.assertEqual(report['first60']['actual_trajectory']['elbow_flex']['span'],2.5)

    def test_empty_or_incomplete_log_is_not_reported_as_zero_motion_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'steps.jsonl').write_bytes(b'{"event":"start"}\n{broken\n')
            report=analyze(folder)
            self.assertEqual(len(report['log']['malformed']),1)
            self.assertIsNone(report['first_goal_s'])
            self.assertEqual(report['first60']['applied_model_trajectory'],{})


if __name__=='__main__':unittest.main()
