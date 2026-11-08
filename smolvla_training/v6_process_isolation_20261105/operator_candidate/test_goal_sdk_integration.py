import unittest
from first_trial_dependencies import prepare
prepare()
from live_safety_runtime import PacketPermission
from individual_reply_guard import StrictIndividualReplies
from test_torque_sdk_integration import FakePort,sdk_handler,send


class Tests(unittest.TestCase):
    def test_read_guard_restored_before_real_sdk_goal_chain(self):
        port=FakePort();handler=sdk_handler();permission=PacketPermission(port,allow_goal=True).install()
        try:
            with StrictIndividualReplies(handler):
                self.assertEqual(handler.readTxRx(port,1,40,1),([1],0,0))
            with permission.goal_once():self.assertEqual(send(handler,port,address=42,width=2,value=1000),0)
            self.assertEqual(permission.goal_packets,1)
            self.assertEqual(port.sent[-1][4:7],bytes((131,42,2)))
            self.assertFalse(any(p[4]==131 and p[5]==40 for p in port.sent))
        finally:permission.seal();permission.restore()

    def test_policy_permission_cannot_enable_torque(self):
        port=FakePort();handler=sdk_handler();permission=PacketPermission(port,allow_goal=True).install()
        try:
            with self.assertRaises(RuntimeError),permission.goal_once():send(handler,port)
            self.assertEqual(port.sent,[]);self.assertEqual(permission.goal_packets,0)
        finally:permission.seal();permission.restore()

    def test_partial_goal_counts_uncertain_one_attempt_and_blocks_retry(self):
        port=FakePort();port.short=True;handler=sdk_handler();permission=PacketPermission(port,allow_goal=True).install()
        try:
            with self.assertRaises(RuntimeError),permission.goal_once():send(handler,port,address=42,width=2,value=1000)
            self.assertNotEqual(send(handler,port,address=42,width=2,value=1000),0)
            port.is_using=False
            with self.assertRaises(RuntimeError):send(handler,port,address=42,width=2,value=1000)
            self.assertEqual(len(port.sent),1);self.assertEqual(permission.goal_packets,1)
        finally:permission.seal();permission.restore()


if __name__=="__main__":unittest.main()
