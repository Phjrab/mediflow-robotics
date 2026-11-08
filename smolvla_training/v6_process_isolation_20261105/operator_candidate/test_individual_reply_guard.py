from types import SimpleNamespace
import unittest
from individual_reply_guard import ReplyRejected, StrictIndividualReplies, read_target_feedback

JOINTS = tuple("abcdef")


def packet(motor=1, payload=b"\xf2\x07", status=0):
    body = bytes((255,255,motor,len(payload)+2,status)) + payload
    return body + bytes(((~sum(body[2:])) & 255,))


class Handler:
    def __init__(self, reply=None):
        self.reply = reply
        self.calls = []
    def txRxPacket(self, port, request):
        self.calls.append(list(request))
        return self.reply or packet(request[2]), 0, 0
    def readTxRx(self, port, motor, address, width):
        reply, comm, error = self.txRxPacket(port, [0,0,motor,4,2,address,width,0])
        return list(reply[5:5+width]), comm, error


class Tests(unittest.TestCase):
    def test_correct_feedback_uses_67_for_six_ids(self):
        handler = Handler()
        bus = SimpleNamespace(packet_handler=handler,port_handler=object(),motors={j:SimpleNamespace(id=i) for i,j in enumerate(JOINTS,1)})
        with StrictIndividualReplies(handler):
            self.assertEqual(read_target_feedback(bus,JOINTS),dict.fromkeys(JOINTS,2034))
        self.assertEqual([r[5] for r in handler.calls],[67]*6)
        self.assertNotIn("txRxPacket",handler.__dict__)

    def test_short_ack_rejected_before_sdk_slice(self):
        handler = Handler(packet(payload=b""))
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.readTxRx(None,1,67,2)

    def test_extra_payload_rejected(self):
        handler = Handler(packet(payload=b"\x01\x02\x03"))
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.readTxRx(None,1,67,2)

    def test_wrong_id_rejected(self):
        handler = Handler(packet(motor=2))
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.readTxRx(None,1,67,2)

    def test_checksum_rejected(self):
        raw = packet(); handler = Handler(raw[:-1]+bytes((raw[-1]^1,)))
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.readTxRx(None,1,67,2)

    def test_motor_error_rejected_even_if_sdk_error_zero(self):
        handler = Handler(packet(status=32))
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.readTxRx(None,1,67,2)

    def test_fault_latches_before_second_transmission(self):
        handler = Handler(packet(payload=b""))
        with StrictIndividualReplies(handler):
            with self.assertRaises(ReplyRejected):handler.readTxRx(None,1,67,2)
            with self.assertRaises(ReplyRejected):handler.readTxRx(None,2,67,2)
        self.assertEqual(len(handler.calls),1)

    def test_write_request_refused_before_transport(self):
        handler = Handler()
        with StrictIndividualReplies(handler), self.assertRaises(ReplyRejected):
            handler.txRxPacket(None,[0,0,1,4,3,40,1,0])
        self.assertEqual(handler.calls,[])

    def test_restore_after_exception(self):
        handler = Handler(); original = handler.txRxPacket
        with self.assertRaises(ValueError):
            with StrictIndividualReplies(handler):raise ValueError("합성 오류")
        self.assertEqual(handler.txRxPacket,original)
        self.assertFalse(hasattr(handler,"_individual_reply_guard"))

    def test_unprotected_feedback_is_refused(self):
        bus = SimpleNamespace(packet_handler=Handler())
        with self.assertRaises(RuntimeError):read_target_feedback(bus,JOINTS)


if __name__ == "__main__":unittest.main()
