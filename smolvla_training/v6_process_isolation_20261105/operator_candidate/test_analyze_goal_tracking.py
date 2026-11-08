import copy
import unittest
from analyze_goal_tracking import summarize

class Tests(unittest.TestCase):
    def fixture(self):
        result=dict(targets=[dict(target=[1.]*6,state=[0.]*6,sent_at=1.),dict(target=[1.]*6,state=[.7]*6,sent_at=1.05)])
        telemetry=[dict(index=i,requested=[1.]*6,sdk_returned=[.9]*6,wire_normalized=[.88]*6,status='SDK_RETURNED') for i in range(2)]
        return result,telemetry
    def test_sdk_encoding_and_observation_are_separate(self):
        result,rows=self.fixture();before=copy.deepcopy(rows);report=summarize(result,rows);joint=report['joints'][0]
        self.assertEqual(joint['sdk_additional_clip_count'],2);self.assertAlmostEqual(joint['sdk_change_max'],.1)
        self.assertAlmostEqual(joint['encoding_change_max'],.02);self.assertAlmostEqual(joint['next_observation_error_mean'],.18)
        self.assertEqual(joint['paired_samples'],1);self.assertEqual(report['final_post_command_observation'],'UNVERIFIED');self.assertEqual(rows,before)
    def test_old_run_without_telemetry_stays_unverified(self):
        result,_=self.fixture();report=summarize(result)
        self.assertEqual(report['status'],'TELEMETRY_MISSING');self.assertEqual(report['motor_fault'],'UNVERIFIED')
    def test_index_or_request_mismatch_is_rejected(self):
        for kind in ('index','target'):
            result,rows=self.fixture()
            if kind=='index':rows[0]['index']=3
            else:rows[0]['requested'][0]=2.
            with self.assertRaises(ValueError):summarize(result,rows)
    def test_failed_attempt_not_claimed_as_motor_acknowledgement(self):
        result,rows=self.fixture();rows[-1]=dict(index=1,requested=[1.]*6,status='ERROR',error_kind='OSError')
        report=summarize(result,rows)
        self.assertEqual(len(report['error_attempts']),1);self.assertEqual(report['motor_fault'],'UNVERIFIED')

if __name__=='__main__':unittest.main()
