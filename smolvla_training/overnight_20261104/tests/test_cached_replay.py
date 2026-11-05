import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cached_action_replay as replay
from test_v5 import args as packet


class Tests(unittest.TestCase):
    def test_only_memory_backend_and_mock_goal_updates(self):
        backend=replay.InMemoryRobot([0.]*5+[20.]);controller=replay.SoftwareOnlyController(backend)
        result=controller.step(packet());self.assertEqual(result['decision'],'VALIDATED_TARGET_ONLY')
        self.assertEqual(backend.register_writes,0);self.assertEqual(backend.state_updates,1)
        with self.assertRaises(ValueError):replay.SoftwareOnlyController(type('Physical',(),{'physical':False})())
        controller.close();self.assertTrue(backend.closed)

    def test_unknown_approval_holds_without_target_and_latches(self):
        backend=replay.InMemoryRobot([0.]*5+[20.]);controller=replay.SoftwareOnlyController(backend);p=packet();p['live_confirmed']=False
        self.assertEqual(controller.step(p)['decision'],'HOLD_NO_WRITE')
        self.assertEqual(controller.step(packet())['decision'],'HOLD_NO_WRITE')
        self.assertEqual(backend.state_updates,0);self.assertTrue(backend.stopped)

    def test_incomplete_and_duplicate_prediction_rows_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.csv'
            for duplicate in (False,True):
                with path.open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=['sample','step','joint','predicted']);w.writeheader()
                    row=dict(sample='x',step=0,joint='shoulder_pan',predicted=0);w.writerow(row)
                    if duplicate:w.writerow(row)
                with self.assertRaises(ValueError):replay.read_predictions(path)

    def test_clip_math_is_not_permission_to_execute(self):
        values=[[2.]*5+[20.] for _ in range(50)]
        report=replay.clip_diagnostic(values,[0.]*5+[20.])
        self.assertEqual(report['nominal_execution_seconds'],50/30)
        self.assertIn('실기',report['simulation'])

    def test_first_step_cannot_skip_home_and_interrupt_is_no_write_hold(self):
        controller=replay.SoftwareOnlyController(replay.InMemoryRobot([0.]*5+[20.]))
        p=packet();p['first']=False
        self.assertEqual(controller.step(p)['decision'],'HOLD_NO_WRITE');self.assertEqual(controller.backend.state_updates,0)
        for error in (RuntimeError('fault'),KeyboardInterrupt()):
            controller=replay.SoftwareOnlyController(replay.InMemoryRobot([0.]*5+[20.]))
            with patch.object(controller.guard,'propose',side_effect=error):
                self.assertEqual(controller.step(packet())['decision'],'HOLD_NO_WRITE')
            self.assertEqual(controller.backend.register_writes,0);self.assertTrue(controller.backend.stopped)


if __name__=='__main__':unittest.main()
