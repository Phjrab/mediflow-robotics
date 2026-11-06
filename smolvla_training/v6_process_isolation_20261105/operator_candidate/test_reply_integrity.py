"""실제 SDK 함수와 가짜 바이트로 재현한다. 장치·모델 import 없음."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from reply_integrity import ReplyFault, StrictSyncReplies


def packet(motor=1, payload=(0,), status=0):
    data = bytes((255, 255, motor, len(payload) + 2, status, *payload))
    return data + bytes(((~sum(data[2:])) & 255,))


def sdk_method(filename, name):
    root = Path(__file__).resolve().parents[1]
    candidates = [root / "goal_incident_20261106/evidence/PROTOCOL_READ_SOURCE.json",
                  root.parent / "goal_incident_20261106/evidence/PROTOCOL_READ_SOURCE.json"]
    fixture = next(p for p in candidates if p.is_file())
    records = json.loads(fixture.read_text(encoding="utf-8"))
    source = next(m["source"] for f in records if f["file"] == filename
                  for m in f["methods"] if m["name"] == name)
    tree = ast.parse(source)
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
    namespace = dict(COMM_SUCCESS=0, COMM_TX_FAIL=-1, COMM_RX_FAIL=-2,
                     COMM_RX_TIMEOUT=-3, COMM_RX_CORRUPT=-4, COMM_NOT_AVAILABLE=-5,
                     PKT_ID=2, PKT_LENGTH=3, PKT_ERROR=4, PKT_PARAMETER0=5,
                     RXPACKET_MAX_LEN=250)
    exec(compile(tree, "보존한 SDK/" + filename, "exec"), namespace)
    return namespace[name]


class BytePort:
    def __init__(self, data):
        self.data = bytearray(data)
        self.reads = 0

    def readPort(self, length):
        self.reads += 1
        result = self.data[:length]
        del self.data[:length]
        return result

    def isPacketTimeout(self):
        return not self.data


class FakeHandler:
    def __init__(self, replies):
        self.replies = list(replies)
        self.rx_calls = 0
        self.tx_calls = 0

    def rxPacket(self, port):
        self.rx_calls += 1
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def syncReadTx(self, *args):
        self.tx_calls += 1
        return 0


class ReplyTests(unittest.TestCase):
    def strict(self, replies):
        handler = FakeHandler(replies)
        reader = SimpleNamespace(ph=handler)
        guard = StrictSyncReplies(reader, handler).install()
        guard.begin("Torque_Enable")
        return guard, handler, reader

    def test_installed_sdk_accepts_ack_checksum_as_torque(self):
        Legacy = type("Legacy", (), {n: sdk_method("protocol_packet_handler.py", n)
                                    for n in ("rxPacket", "readRx")})
        ack = packet(payload=())
        values, comm, error = Legacy().readRx(BytePort(ack), 1, 1)
        self.assertEqual((comm, error), (0, 0))
        self.assertEqual(values, [ack[-1]])  # 데이터가 아닌 체크섬을 값으로 반환
        self.assertNotIn(values[0], (0, 1))

    def test_installed_sdk_truncates_long_reply(self):
        Legacy = type("Legacy", (), {n: sdk_method("protocol_packet_handler.py", n)
                                    for n in ("rxPacket", "readRx")})
        self.assertEqual(Legacy().readRx(BytePort(packet(payload=(1, 8))), 1, 1), ([1], 0, 0))

    def test_installed_group_ignores_status_error(self):
        Reader = type("Reader", (), {n: sdk_method("group_sync_read.py", n)
                                    for n in ("rxPacket", "getData", "isAvailable")})
        reader = Reader()
        reader.port = None
        reader.ph = SimpleNamespace(readRx=lambda *args: ([0], 0, 32))
        reader.data_dict, reader.start_address, reader.data_length = {1: []}, 40, 1
        self.assertEqual(reader.rxPacket(), 0)
        self.assertEqual(reader.getData(1, 40, 1), 0)

    def test_valid_zero_and_one_unchanged(self):
        for value in (0, 1):
            with self.subTest(value=value):
                guard, handler, reader = self.strict([(packet(payload=(value,)), 0)])
                self.assertEqual(guard.readRx(None, 1, 1), ([value], 0, 0))
                self.assertEqual(handler.rx_calls, 1)
                guard.restore()
                self.assertIs(reader.ph, handler)

    def test_short_ack_refused_with_raw_packet(self):
        raw = packet(payload=())
        guard, handler, _ = self.strict([(raw, 0)])
        with self.assertRaisesRegex(ReplyFault, "길이") as error:
            guard.readRx(None, 1, 1)
        self.assertEqual(error.exception.observed["raw_hex"], raw.hex())
        self.assertEqual(handler.rx_calls, 1)

    def test_long_reply_refused(self):
        guard, _, _ = self.strict([(packet(payload=(1, 8)), 0)])
        with self.assertRaisesRegex(ReplyFault, "길이"):
            guard.readRx(None, 1, 1)

    def test_wrong_id_refused_without_skipping_or_rereading(self):
        guard, handler, _ = self.strict([(packet(motor=2), 0)])
        with self.assertRaisesRegex(ReplyFault, "ID"):
            guard.readRx(None, 1, 1)
        self.assertEqual(handler.rx_calls, 1)

    def test_bad_checksum_refused(self):
        raw = bytearray(packet());raw[-1] ^= 1
        guard, _, _ = self.strict([(raw, 0)])
        with self.assertRaisesRegex(ReplyFault, "체크섬"):
            guard.readRx(None, 1, 1)

    def test_status_error_refused_instead_of_returning_zero(self):
        guard, _, _ = self.strict([(packet(status=32), 0)])
        with self.assertRaisesRegex(ReplyFault, "모터 오류") as error:
            guard.readRx(None, 1, 1)
        self.assertEqual(error.exception.observed["status"], 32)

    def test_comm_error_latches_no_more_reads_or_transmits(self):
        guard, handler, _ = self.strict([(packet(), -4)])
        with self.assertRaises(ReplyFault):guard.readRx(None, 1, 1)
        with self.assertRaises(ReplyFault):guard.readRx(None, 1, 1)
        with self.assertRaises(ReplyFault):guard.syncReadTx(None)
        self.assertEqual((handler.rx_calls, handler.tx_calls), (1, 0))

    def test_receive_exception_no_retry(self):
        guard, handler, _ = self.strict([ConnectionError("합성 수신 실패")])
        with self.assertRaisesRegex(ReplyFault, "수신 예외"):
            guard.readRx(None, 1, 1)
        self.assertEqual(handler.rx_calls, 1)

    def test_invalid_bytes_and_boolean_communication_refused(self):
        for data, code in (("invalid", 0), (packet(), False)):
            with self.subTest(data=data):
                guard, _, _ = self.strict([(data, code)])
                with self.assertRaises(ReplyFault):guard.readRx(None, 1, 1)

    def test_second_motor_failure_stops_group_before_third(self):
        Reader = type("Reader", (), {"rxPacket": sdk_method("group_sync_read.py", "rxPacket")})
        reader = Reader();reader.port = None;reader.data_dict = {1: [], 2: [], 3: []};reader.data_length = 1
        handler = FakeHandler([(packet(motor=1), 0), (packet(motor=2, payload=()), 0)])
        reader.ph = handler
        StrictSyncReplies(reader, handler).install().begin("Torque_Enable")
        with self.assertRaises(ReplyFault):reader.rxPacket()
        self.assertEqual(handler.rx_calls, 2)
        self.assertFalse(reader.last_result)

    def test_no_motor_write_method_exposed(self):
        guard, handler, _ = self.strict([])
        for name in ("writeTxRx", "syncWriteTxOnly", "enable_torque", "send_action"):
            self.assertFalse(hasattr(guard, name))
        self.assertEqual((handler.rx_calls, handler.tx_calls), (0, 0))


if __name__ == "__main__":
    unittest.main()
