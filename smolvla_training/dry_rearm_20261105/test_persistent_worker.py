import threading
import time
import unittest
from persistent_policy_worker import PersistentPolicyWorker

class PersistentWorkerTests(unittest.TestCase):
    def test_warmup_and_prediction_share_worker_not_caller(self):
        worker=PersistentPolicyWorker()
        try:
            ids=[worker.call(threading.get_ident,label=label) for label in ("warm","real")]
            self.assertEqual(ids[0],ids[1]);self.assertNotEqual(ids[0],threading.get_ident())
            self.assertEqual([r["label"] for r in worker.history],["warm","real"])
        finally:self.assertTrue(worker.close())
    def test_failure_propagates_and_close_rejects_new_jobs(self):
        worker=PersistentPolicyWorker()
        def fail():raise ValueError("NaN")
        with self.assertRaises(ValueError):worker.call(fail)
        self.assertTrue(worker.close())
        with self.assertRaises(RuntimeError):worker.call(lambda:1)
    def test_concurrent_prediction_rejected(self):
        worker=PersistentPolicyWorker();started=threading.Event();release=threading.Event()
        def work():started.set();release.wait(.3)
        caller=threading.Thread(target=lambda:worker.call(work));caller.start();started.wait(1)
        try:
            with self.assertRaises(RuntimeError):worker.call(lambda:None)
        finally:release.set();caller.join();self.assertTrue(worker.close())
    def test_close_wait_is_bounded_and_late_work_has_no_motor_api(self):
        worker=PersistentPolicyWorker();started=threading.Event();release=threading.Event()
        caller=threading.Thread(target=lambda:worker.call(lambda:(started.set(),release.wait(.3))))
        caller.start();started.wait(1)
        try:self.assertFalse(worker.close(.005))
        finally:release.set();caller.join();self.assertTrue(worker.close())

if __name__=="__main__":unittest.main()
