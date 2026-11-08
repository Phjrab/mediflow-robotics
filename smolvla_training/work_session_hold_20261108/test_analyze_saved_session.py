import copy
import unittest
from analyze_saved_session import analyze

class Tests(unittest.TestCase):
    def fixture(self):
        rows=[dict(state=[0.]*6,target=[.5]*6,raw_action=[1.]*6,clip=True,sent_at=float(i)) for i in range(2)]
        result=dict(targets=rows,status='HOLD',torque_register_writes=0,ports_closed={'io':True},cleanup_errors=[],error={'reason':'큰 목표 점프'},
            last_target_context={'raw_action':[0.,0.,-12.,0.,0.,0.],'measured_state':[0.]*6,'previous_target':[0.]*6})
        return result,[dict(chunk=0,status='HOLD',sent_count=2,elapsed_seconds=3.,prediction={'inference_seconds':1.})]
    def test_jump_joint_and_tracking_and_no_mutation(self):
        result,chunks=self.fixture();before=copy.deepcopy(result);report=analyze(result,chunks)
        self.assertEqual(report['offending_joints'][0]['joint'],'elbow_flex');self.assertEqual(report['offending_joints'][0]['raw_current_difference'],12)
        self.assertEqual(report['joints'][0]['observed_span'],0);self.assertEqual(report['joints'][0]['requested_minus_current_mean_abs'],.5)
        self.assertEqual(result,before)
    def test_nonnumeric_or_nan_rejected(self):
        for value in (float('nan'),'1',True):
            result,chunks=self.fixture();result['targets'][0]['state'][0]=value
            with self.assertRaises(ValueError):analyze(result,chunks)
    def test_mismatched_chunk_count_rejected(self):
        result,chunks=self.fixture();chunks[0]['sent_count']=1
        with self.assertRaises(ValueError):analyze(result,chunks)
    def test_non_jump_stop_not_claimed_as_jump(self):
        result,chunks=self.fixture();result.pop('last_target_context');result['error']['reason']='온도 상한'
        self.assertEqual(analyze(result,chunks)['offending_joints'],[])

if __name__=='__main__':unittest.main()
