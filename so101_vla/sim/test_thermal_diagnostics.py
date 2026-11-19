"""실제 장치를 열지 않는 원문·진단 필터·보호·READ 패킷 경로 검사."""
import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from thermal_diagnostics import ThermalDiagnostics
from servo_thermal_readonly import JOINTS, LinuxReadTransport, ReadFault, decode_reply, read_request, require_unowned_port, run
import direct_live as base
from home_config_server import thermal_snapshot
from thermal_read import read_temperatures
from individual_reply_guard import StrictIndividualReplies


class Clock:
    now = 0.
    def time(self):
        return self.now
    def sleep(self, seconds):
        self.now += max(seconds, 0)


class FakeBus:
    def __init__(self, scenario='normal'):
        self.scenario = scenario
        self.fd = None
        self.cleanup_errors = []
        self.packets = []
        self.cycle = 0

    def open(self):
        self.fd = 1

    def close(self):
        self.fd = None

    def request(self, packet, width):
        mid, address = packet[2], packet[5]
        assert packet == read_request(mid, address, width)
        self.packets.append(packet)
        if self.scenario == 'interrupt' and len(self.packets) == 20:
            raise KeyboardInterrupt()
        if self.scenario == 'communication' and len(self.packets) == 20:
            raise ReadFault('READ 통신 timeout', communication=-6, raw_hex='')
        if address == 40:
            data = bytes((int(self.scenario == 'torque_on' and mid == 4),))
        else:
            temp = 34
            if mid == 6:
                if self.scenario == 'spike' and self.cycle == 5:
                    temp = 81
                elif self.scenario == 'gradual':
                    temp = min(80, 34 + self.cycle / 10)
            data = bytearray(15)
            data[0:2] = (2000).to_bytes(2, 'little')
            data[6], data[7] = 122, round(temp)
            data[9] = int(self.scenario == 'status' and mid == 3)
            data = bytes(data)
            if mid == 6:
                self.cycle += 1
        head = bytes((255, 255, mid, width + 2, 0)) + data
        raw = head + bytes(((~sum(head[2:])) & 255,))
        if self.scenario == 'checksum' and len(self.packets) == 20:
            raw = raw[:-1] + bytes((raw[-1] ^ 1,))
        return raw


class ThermalDiagnosticTests(unittest.TestCase):
    def axis(self, observer, now, temp):
        return observer.observe_axis('gripper', now, temp, 12.2)

    @staticmethod
    def wire_reply(mid, address, width, value, *, status=0):
        payload = bytearray(width)
        payload[63 - address] = value
        head = bytes((255, 255, mid, width + 2, status)) + payload
        return head + bytes(((~sum(head[2:])) & 255,))

    def test_high_wire_reads_are_matched_by_packet_and_deduplicated_by_sequence(self):
        d = ThermalDiagnostics(JOINTS)
        direct = self.wire_reply(2, 56, 15, 100)
        individual = self.wire_reply(2, 63, 1, 109)
        base_event = {'event': 'wire_rx', 'expected_id': 2, 'communication': 0,
                      'monotonic_s': 1.0, 'unix': 2.0}
        block = dict(base_event, operation='read:56:15', raw_hex=direct.hex())
        read = dict(base_event, operation='read:63:1', raw_hex=individual.hex())
        d.record_wire_reply(block, sequence=1)
        d.record_wire_reply(block, sequence=1)
        d.record_wire_reply(read, sequence=2)
        with self.assertRaisesRegex(RuntimeError, 'later read'), d.recheck():
            d.record_wire_reply(read, sequence=2)
            d.record_wire_reply(read, sequence=3)
            raise RuntimeError('later read')
        d.record_wire_reply(read, sequence=4)
        summary = json.loads(json.dumps(d.summary(), allow_nan=False))['temperature_read_errors_100c']
        self.assertEqual((summary['threshold_c'], summary['count']), (100, 4))
        joint = summary['joints']['shoulder_lift']
        self.assertEqual((joint['count'], joint['direct_count'], joint['recheck_count']), (4, 3, 1))
        self.assertEqual([packet['phase'] for packet in joint['packets']], ['direct', 'direct', 'recheck', 'direct'])
        self.assertEqual([packet['raw_hex'] for packet in joint['packets']], [direct.hex(), *[individual.hex()] * 3])
        self.assertEqual([packet['temperature_c'] for packet in joint['packets']], [100, 109, 109, 109])
        self.assertEqual(summary['joints']['gripper']['packets'], [])

    def test_does_not_attribute_unmatched_or_corrupt_wire_replies(self):
        d = ThermalDiagnostics(JOINTS)
        packet = self.wire_reply(2, 63, 1, 120)
        event = {'event': 'wire_rx', 'operation': 'read:63:1', 'expected_id': 2,
                 'communication': 0, 'raw_hex': packet.hex()}
        for sequence, changed in enumerate((
            {'raw_hex': ''}, {'raw_hex': 'invalid'}, {'raw_hex': packet[:-1].hex()},
            {'raw_hex': (packet[:-1] + bytes((packet[-1] ^ 1,))).hex()},
            {'operation': 'read:33:8'}, {'operation': 'read:56:15'},
            {'expected_id': 4}, {'communication': -7}, {'event': 'thermal_cycle'},
            {'raw_hex': self.wire_reply(2, 63, 1, 99).hex()},
        ), 1):
            d.record_wire_reply(dict(event, **changed), sequence=sequence)
        self.assertEqual(d.summary()['temperature_read_errors_100c']['count'], 0)

    def test_valid_high_reply_with_status_is_preserved_without_overriding_status_handling(self):
        d = ThermalDiagnostics(JOINTS)
        packet = self.wire_reply(6, 56, 15, 150, status=32)
        d.record_wire_reply({'event': 'wire_rx', 'operation': 'read:56:15', 'expected_id': 6,
                             'communication': 0, 'raw_hex': packet.hex()}, sequence=1)
        row, = d.summary()['temperature_read_errors_100c']['joints']['gripper']['packets']
        self.assertEqual((row['status'], row['temperature_c'], row['raw_hex']), (32, 150, packet.hex()))
        self.assertEqual(d.errors['total'], 0)

    def test_single_spike_is_visible_but_never_authorizes_motion(self):
        d = ThermalDiagnostics(JOINTS)
        rows = [self.axis(d, i * .1, temp) for i, temp in enumerate([34, 81, 34])]
        self.assertEqual([row['raw_c'] for row in rows], [34, 81, 34])
        self.assertEqual([row['filtered_c'] for row in rows], [34, 34, 34])
        self.assertTrue(rows[1]['suspected_spike'])
        self.assertFalse(rows[1]['candidate_filtered_hold'])
        self.assertTrue(rows[1]['raw_hold_required'])
        self.assertFalse(rows[1]['filter_controls_motion'])
        summary = d.summary()['joints']['gripper']
        self.assertEqual((summary['raw_peak_c'], summary['filtered_peak_c'], summary['spike_count']), (81, 34, 1))

    def test_actual_55_gate_still_stops_single_81(self):
        sample = dict(temperatures=dict.fromkeys(JOINTS, 34), statuses=dict.fromkeys(JOINTS, 0))
        sample['temperatures']['gripper'] = 81
        original = copy.deepcopy(sample)
        d = ThermalDiagnostics(JOINTS)
        d.observe(0, dict.fromkeys(JOINTS, 34), dict.fromkeys(JOINTS, 12.2), sample['statuses'])
        d.observe(.1, sample['temperatures'], dict.fromkeys(JOINTS, 12.2), sample['statuses'])
        self.assertEqual(sample, original)
        with self.assertRaises(base.Stop):
            base.feedback_gate(sample)

    def test_slow_heating_is_not_rejected(self):
        d = ThermalDiagnostics(JOINTS)
        rows = [self.axis(d, i * .1, 34 + i * .1) for i in range(230)]
        self.assertFalse(any(row['suspected_spike'] for row in rows))
        self.assertTrue(rows[-1]['candidate_filtered_hold'])
        self.assertTrue(rows[-1]['raw_hold_required'])

    def test_candidate_needs_three_and_more_than_point_three_seconds(self):
        d = ThermalDiagnostics(JOINTS)
        self.assertFalse(self.axis(d, 0, 55)['candidate_filtered_hold'])
        self.assertFalse(self.axis(d, .1, 55)['candidate_filtered_hold'])
        self.assertFalse(self.axis(d, .3, 55)['candidate_filtered_hold'])
        self.assertTrue(self.axis(d, .31, 55)['candidate_filtered_hold'])

    def test_two_raw_70_override_diagnostic_filter(self):
        d = ThermalDiagnostics(JOINTS)
        self.axis(d, 0, 34)
        self.assertFalse(self.axis(d, .1, 81)['candidate_filtered_hold'])
        self.assertTrue(self.axis(d, .2, 82)['candidate_filtered_hold'])

    def test_thresholds_are_strict(self):
        d = ThermalDiagnostics(JOINTS)
        self.axis(d, 0, 34)
        self.assertFalse(self.axis(d, .2, 39)['suspected_spike'])
        self.assertTrue(self.axis(d, .4, 40)['suspected_spike'])

    def test_window_expires(self):
        d = ThermalDiagnostics(JOINTS)
        self.axis(d, 0, 34)
        self.assertFalse(self.axis(d, 1.01, 40)['suspected_spike'])

    def test_invalid_numbers_and_time(self):
        for value in [float('nan'), float('inf'), True, -1, 256]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.axis(ThermalDiagnostics(JOINTS), 0, value)
        d = ThermalDiagnostics(JOINTS)
        self.axis(d, 1, 34)
        with self.assertRaises(ValueError):
            self.axis(d, 1, 35)

    def test_other_axis_source_max_is_preserved(self):
        d = ThermalDiagnostics(JOINTS)
        row = d.observe_axis('wrist_roll', 0, 34, 12.2, source_values=[81, 34])
        self.assertEqual(row['raw_peak_c'], 81)
        self.assertEqual(row['filtered_c'], 34)

    def test_cached_site_data_never_opens_bus_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'status.json'
            p.write_text(json.dumps(dict(updated=100, phase='moving', thermal_diagnostics={'gripper': {'raw_c': 34}})))
            self.assertTrue(thermal_snapshot(p, 100.2)['available'])
            self.assertFalse(thermal_snapshot(p, 102)['available'])
            p.write_text('not json')
            self.assertFalse(thermal_snapshot(p, 102)['available'])


class ReadonlyTransportTests(unittest.TestCase):
    def test_jetson_fuser_free_port_without_unsupported_separator(self):
        seen = []
        def runner(argv, **kwargs):
            seen.append(argv)
            return SimpleNamespace(returncode=1, stdout='', stderr='')
        require_unowned_port('/dev/ttyACM1', runner)
        self.assertEqual(seen, [['sudo', '-n', 'fuser', '/dev/ttyACM1']])

    def test_port_owner_or_query_error_still_refuses(self):
        for code, stdout, stderr in [(0, '123', ''), (1, '', 'usage error'), (2, '', 'sudo required')]:
            with self.subTest(code=code), self.assertRaises(ReadFault):
                require_unowned_port('/dev/ttyACM1', lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr))

    def test_leader_port_can_never_replace_follower(self):
        for port in ['/dev/ttyACM0', '/dev/ttyACM1', '/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6085272-if00']:
            with self.subTest(port=port), self.assertRaises(ValueError):
                LinuxReadTransport(port)

    def simulate(self, scenario, duration=3):
        bus, clock, rows = FakeBus(scenario), Clock(), []
        result = run(bus, rows.append, duration, 10, clock=clock.time, pause=clock.sleep)
        self.assertTrue(result['port_closed'])
        self.assertEqual(result['motor_register_writes'], 0)
        self.assertTrue(all(packet[4] == 2 for packet in bus.packets))
        return result, rows

    def test_full_120_seconds_virtual_read(self):
        result, rows = self.simulate('normal', 120)
        self.assertEqual(result['status'], 'READONLY_COMPLETE')
        self.assertGreaterEqual(result['cycles'], 1199)
        self.assertEqual(result['thermal_diagnostics']['joints']['gripper']['spike_count'], 0)

    def test_single_spike_stops_hardware_path_while_shadow_reports_it(self):
        result, rows = self.simulate('spike')
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(result['error']['observed']['temperature_c'], 81)
        samples = [row for row in rows if row['event'] == 'thermal_readonly_sample']
        self.assertTrue(samples[-1]['diagnostics']['suspected_spike'])
        self.assertEqual(samples[-1]['voltage_v'], 12.2)
        self.assertIn('rx_hex', samples[-1])

    def test_gradual_heat_stops_at_raw_55(self):
        result, _ = self.simulate('gradual', 30)
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(result['error']['observed']['temperature_c'], 55)

    def test_faults_close_without_any_register_write(self):
        for scenario, kind in [('communication', 'communication'), ('checksum', 'checksum'), ('status', 'other')]:
            with self.subTest(scenario=scenario):
                result, rows = self.simulate(scenario)
                self.assertEqual(result['status'], 'HOLD')
                self.assertEqual(result['thermal_diagnostics']['errors'][kind], 1)
                self.assertEqual(rows[-1]['event'], 'readonly_stop')
                if scenario in ('communication', 'checksum'):
                    self.assertIn('expected_id', result['error']['observed'])
                    self.assertIn('tx_hex', result['error']['observed'])

    def test_torque_on_stops_without_turning_it_off(self):
        result, _ = self.simulate('torque_on')
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(result['read_packet_attempts'], 4)
        self.assertEqual(result['error']['observed']['joint'], 'wrist_flex')

    def test_ctrl_c_always_closes(self):
        result, _ = self.simulate('interrupt')
        self.assertEqual(result['status'], 'STOPPED')

    def test_write_addresses_cannot_be_requested(self):
        for address, width in [(42, 2), (40, 2), (16, 2), (28, 2), (55, 15)]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                read_request(1, address, width)

    def test_corrupt_reply_rejected_with_raw_bytes(self):
        with self.assertRaises(ReadFault) as fault:
            decode_reply(bytes.fromhex('ffff01030000fa'), 1, 1)
        self.assertEqual(fault.exception.observed['raw_hex'], 'ffff01030000fa')


class ExistingReadIntegrationTests(unittest.TestCase):
    def bus(self, values, corrupt=False):
        class Handler:
            requests = []
            def txRxPacket(self, port, request):
                self.requests.append(bytes(request))
                mid = request[2]
                value = values[mid].pop(0)
                head = bytes((255, 255, mid, 3, 0, value))
                checksum = ((~sum(head[2:])) & 255) ^ int(corrupt)
                return list(head + bytes((checksum,))), 0, 0
            def readTxRx(self, port, mid, address, width):
                packet, comm, status = self.txRxPacket(port, list(bytes((255, 255, mid, 4, 2, address, width, 0))))
                return packet[5:-1], comm, status
        return SimpleNamespace(packet_handler=Handler(), port_handler=SimpleNamespace(getBytesAvailable=lambda: 0))

    def test_every_reply_is_logged_without_changing_temperature(self):
        bus = self.bus({i: [34] for i in range(1, 7)})
        traces = []
        temps, differences = read_temperatures(bus, dict(zip(JOINTS, range(1, 7))),
                                               dict.fromkeys(JOINTS, 34), StrictIndividualReplies, traces=traces)
        self.assertEqual(temps, dict.fromkeys(JOINTS, 34))
        self.assertEqual(differences, {})
        self.assertEqual(len(traces), 6)
        self.assertTrue(all('raw_hex' in row and row['communication'] == 0 for row in traces))

    def test_fresh_individual_value_and_all_source_bytes_are_preserved(self):
        values = {i: [34] for i in range(1, 7)}
        values[6] = [81]
        bus, traces = self.bus(values), []
        temps, differences = read_temperatures(bus, dict(zip(JOINTS, range(1, 7))),
                                               dict.fromkeys(JOINTS, 34), StrictIndividualReplies, traces=traces)
        self.assertEqual(temps['gripper'], 81)
        self.assertEqual(differences['gripper']['individual_reads_c'], [81])
        self.assertEqual(len(traces), 6)
        self.assertTrue(traces[-1]['checksum_valid'])
        self.assertEqual(traces[-1]['received_id'], 6)
        self.assertTrue(all(packet[4] == 2 for packet in bus.packet_handler.requests))

    def test_bulk_high_never_substitutes_for_the_individual_register(self):
        temps, _ = read_temperatures(self.bus({2: [33]}), {'shoulder_lift': 2},
                                     {'shoulder_lift': 81}, StrictIndividualReplies)
        self.assertEqual(temps['shoulder_lift'], 33)

    def test_same_axis_requests_are_at_least_20ms_apart(self):
        now, previous, traces = [0.], {}, []
        bus = self.bus({2: [60, 60, 33]})
        values = []
        for _ in range(3):
            temps, _ = read_temperatures(bus, {'shoulder_lift': 2}, {'shoulder_lift': 34},
                StrictIndividualReplies, traces=traces, last_requests=previous,
                clock=lambda:now[0], pause=lambda duration:now.__setitem__(0,now[0]+duration))
            values.append(temps['shoulder_lift'])
        self.assertEqual(values,[60,60,33])
        self.assertEqual([x['request_started_monotonic_s'] for x in traces],[0.,.02,.04])
        self.assertTrue(all(x['checksum_valid'] and x['received_id']==2 for x in traces))
        self.assertEqual(len(bus.packet_handler.requests),3)

    def test_missing_individual_response_remains_an_error(self):
        values = {i: [34] for i in range(1, 7)}
        values[2] = []
        with self.assertRaises(Exception):
            read_temperatures(self.bus(values), dict(zip(JOINTS, range(1, 7))),
                              dict.fromkeys(JOINTS, 34), StrictIndividualReplies)

    def test_invalid_reply_is_preserved_and_not_retried(self):
        bus, traces = self.bus({i: [34] for i in range(1, 7)}, corrupt=True), []
        with self.assertRaises(RuntimeError):
            read_temperatures(bus, dict(zip(JOINTS, range(1, 7))), dict.fromkeys(JOINTS, 34),
                              StrictIndividualReplies, traces=traces)
        self.assertEqual(len(bus.packet_handler.requests), 1)
        self.assertIn('raw_hex', traces[0]['observed'])


class LiveTemperatureSummaryTests(unittest.TestCase):
    def test_shared_io_preserves_matching_replies_even_when_a_later_read_fails(self):
        import raw_live
        import stream_live

        clock, logged = Clock(), []

        class MockBaseIO:
            def __init__(self, context, policy):
                self.wire_fault = False

        camera_modules = {name: ModuleType(name) for name in (
            'lerobot', 'lerobot.cameras', 'lerobot.cameras.opencv', 'lerobot.cameras.opencv.camera_opencv',
        )}
        camera_modules['lerobot.cameras.opencv'].OpenCVCameraConfig = object
        camera_modules['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera = object
        with patch.dict(sys.modules, camera_modules), patch.object(base, 'device_class', return_value=MockBaseIO):
            self.assertIs(raw_live.legacy.device_class, stream_live.device_class)
            io_class = stream_live.device_class()
        io_class.clock, io_class.pause = staticmethod(clock.time), staticmethod(clock.sleep)
        io = io_class({'contract': {'cameras': {}}}, logged.append, None)
        self.addCleanup(io.camera_logger.removeHandler, io.cameras_stats)
        io.ids = dict(zip(JOINTS, range(1, 7)))

        reply = ThermalDiagnosticTests.wire_reply
        block = reply(2, 56, 15, 120)
        healthy_checksum = reply(2, 63, 1, 120)
        corrupt = healthy_checksum[:-1] + bytes((healthy_checksum[-1] ^ 1,))
        later_direct = reply(2, 63, 1, 121)
        bulk = iter(((block, 0), (reply(3, 56, 15, 130)[:-1], 0)))
        individual = iter((healthy_checksum, corrupt, later_direct))

        class Handler:
            def rxPacket(self, port):
                return next(bulk)

            def txRxPacket(self, port, request):
                return next(individual), 0, 0

            def readTxRx(self, port, motor, address, width):
                packet, communication, status = self.txRxPacket(port, bytes((255, 255, motor, 4, 2, address, width, 0)))
                return packet[5:-1], communication, status

        port = SimpleNamespace(isPacketTimeout=lambda: False, getBytesAvailable=lambda: 0, is_using=False)
        handler = Handler()
        io.bus = SimpleNamespace(packet_handler=handler, port_handler=port)
        with self.assertRaises(base.Stop):
            base.read_sync_replies(handler, port, {'shoulder_lift': 2, 'elbow_flex': 3}, 56, 15, io.communication)
        io.temperature_fault = {'shoulder_lift': 120}
        io.confirm_thermal_stop()
        self.assertIn('read_error', io.thermal_recheck['shoulder_lift'])
        self.assertEqual(io.thermal_recheck['shoulder_lift']['reply_trace'][0]['raw_hex'], healthy_checksum.hex())
        temps, _ = read_temperatures(io.bus, {'shoulder_lift': 2}, {'shoulder_lift': 34},
                                     StrictIndividualReplies, last_requests=io.temperature_last_requests,
                                     clock=clock.time, pause=clock.sleep, retry=io.communication)
        self.assertEqual(temps['shoulder_lift'], 121)
        data = json.loads(json.dumps({'thermal_diagnostics': io.thermal_diagnostics.summary()}, allow_nan=False))
        totals = data['thermal_diagnostics']['temperature_read_errors_100c']
        self.assertEqual(totals['count'], 3)
        axis = totals['joints']['shoulder_lift']
        self.assertEqual((axis['direct_count'], axis['recheck_count']), (2, 1))
        self.assertEqual([p['raw_hex'] for p in axis['packets']], [block.hex(), healthy_checksum.hex(), later_direct.hex()])
        self.assertEqual([p['sequence'] for p in axis['packets']], [1, 3, 5])
        self.assertEqual(totals['joints']['elbow_flex']['count'], 0)
        self.assertEqual(sum(row['event'] == 'wire_rx' for row in logged), 5)

    def test_live_entrypoints_export_the_same_diagnostics_summary(self):
        import raw_live
        import stream_live

        for module in (raw_live, stream_live):
            with self.subTest(entry=module.__name__):
                tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
                live = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'live')
                self.assertIn('io.thermal_diagnostics.summary()', ast.unparse(live))


if __name__ == '__main__':
    unittest.main()
