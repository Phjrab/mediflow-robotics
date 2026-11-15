import unittest
from servo_ping_readonly import ping_packet, require_ping_packet, parse_ping_reply, scan
from servo_thermal_readonly import FOLLOWER, ReadFault


class PingReadonlyTests(unittest.TestCase):
    def test_exact_ping_and_no_other_opcode(self):
        self.assertEqual(ping_packet(1).hex(), 'ffff010201fb')
        for mid in range(1, 7):
            require_ping_packet(ping_packet(mid))
        for raw in [bytes.fromhex('ffff0104022801cf'), bytes.fromhex('ffff010203f9'), b'']:
            with self.assertRaises((ReadFault, ValueError)):
                require_ping_packet(raw)

    def test_reply_status_and_checksum(self):
        self.assertEqual(parse_ping_reply(bytes.fromhex('ffff010200fc'), 1), 0)
        self.assertEqual(parse_ping_reply(bytes.fromhex('ffff010220dc'), 1), 32)
        for raw in [bytes.fromhex('ffff010200fd'), bytes.fromhex('ffff020200fb'), b'']:
            with self.assertRaises(ReadFault):
                parse_ping_reply(raw, 1)

    def fake(self, absent=(), bad=()):
        seen = []
        class Fake:
            fd = None
            cleanup_errors = []
            def __init__(self, port):
                assert port == FOLLOWER
            def open(self):
                self.fd = 1
            def close(self):
                self.fd = None
            def ping(self, mid):
                seen.append(mid)
                if mid in bad:
                    raise ReadFault('가짜 응답 오류')
                return {'id': mid, 'responded': mid not in absent, 'normal': mid not in absent}
        return Fake, seen

    def test_id1_silence_does_not_skip_id2_through_id6(self):
        fake, seen = self.fake(absent=[1])
        result = scan(fake)
        self.assertEqual(seen, [1,2,3,4,5,6])
        self.assertEqual(result['responding_ids'], [2,3,4,5,6])
        self.assertFalse(result['all_normal'])
        self.assertEqual(result['motor_register_writes'], 0)

    def test_all_six_respond(self):
        fake, seen = self.fake()
        result = scan(fake)
        self.assertTrue(result['all_normal'])
        self.assertTrue(all(row['port_closed'] for row in result['ids']))

    def test_separate_id_errors_are_reported(self):
        fake, seen = self.fake(bad=[1,4])
        result = scan(fake)
        self.assertEqual(seen, [1,2,3,4,5,6])
        self.assertEqual(result['responding_ids'], [2,3,5,6])


if __name__ == '__main__':
    unittest.main()
