import unittest
import tempfile
import types
from pathlib import Path
from unittest.mock import patch
from recorder_bus_readonly import allow_read, verify_off, distribution, close_bus_readonly, run_phase
from config_runtime import load
from test_communication_retry import Clock
from test_id_recovery import BufferedPort


def packet(mid, instruction, parameters):
    data = bytes([mid, len(parameters)+2, instruction, *parameters])
    return b'\xff\xff'+data+bytes([255-sum(data)%256])


class ReadonlyComparisonTests(unittest.TestCase):
    def test_only_expected_fresh_reads_allowed(self):
        ids = [1, 2, 3, 4, 6, 5]
        for mid in ids:
            self.assertEqual(allow_read(packet(mid, 2, [56, 15]), ids), packet(mid, 2, [56, 15]))
            allow_read(packet(mid, 2, [63, 1]), ids)
        allow_read(packet(254, 0x82, [33, 8]+ids), ids)
        for value in (b'', packet(1, 3, [40, 0]), packet(1, 3, [42, 0, 8]),
                      packet(254, 0x83, [42, 2, 1, 0, 8]), packet(1, 2, [42, 2]),
                      packet(254, 0x82, [56, 15, 1])):
            with self.assertRaises(RuntimeError): allow_read(value, ids)

    def test_torque_on_or_temperature_or_status_never_silently_passes(self):
        joints=load()['joint_names']
        modes, data = {j:bytearray(8) for j in joints}, {j:bytearray(15) for j in joints}
        for row in data.values():row[7]=34
        verify_off(modes, data)
        for target, index, value in ((modes, 7, 1), (modes, 0, 1), (data, 7, 55), (data, 9, 32)):
            previous = target['elbow_flex'][index]
            target['elbow_flex'][index] = value
            with self.assertRaisesRegex(RuntimeError, 'elbow_flex'): verify_off(modes, data)
            target['elbow_flex'][index] = previous
        with self.assertRaisesRegex(RuntimeError,'6축'):
            verify_off({}, {})
        hot=dict.fromkeys(joints,34);hot['wrist_roll']=55
        with self.assertRaisesRegex(RuntimeError,'wrist_roll'):
            verify_off(modes,data,hot)

    def test_empty_timing_does_not_claim_zero_latency(self):
        self.assertIsNone(distribution([])['p95'])
        self.assertEqual(distribution([.01, .02, .03])['max'], .03)

    def test_direct_port_close_falls_back_without_calling_torque_cleanup(self):
        calls=[]
        serial=types.SimpleNamespace(is_open=True)
        def serial_close():calls.append('serial');serial.is_open=False
        serial.close=serial_close
        def broken_close():raise OSError('close failed')
        bus=types.SimpleNamespace(port_handler=types.SimpleNamespace(closePort=broken_close,ser=serial),
                                  disconnect=lambda **kw: self.fail('bus.disconnect must not be called'))
        closed,errors=close_bus_readonly(bus)
        self.assertTrue(closed);self.assertEqual(calls,['serial'])
        self.assertEqual(errors[0]['operation'],'closePort')

    def test_phase_cleanup_and_actual_packet_guard_with_recorder_fault(self):
        config=load();joints=config['joint_names'];clock=Clock();port=BufferedPort();packets=[];queue=[]
        order={j:mid for mid in config['communication']['read_order'] for j in joints if joints.index(j)+1==mid}
        def write(raw):
            packets.append(allow_read(raw,list(order.values())));return len(raw)
        port.writePort=write
        def sync(p,address,width,ids,size):
            p.writePort(packet(254,0x82,[address,width]+ids))
            for mid in ids:
                payload=bytearray(width)
                if address==56:payload[7]=34;payload[6]=120
                queue.append((packet(mid,0,list(payload)),0))
            return 0
        handler=types.SimpleNamespace(syncReadTx=sync,rxPacket=lambda p:queue.pop(0))
        bus=types.SimpleNamespace(port_handler=port,packet_handler=handler)
        recorder=types.SimpleNamespace(capture=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('enqueue failed')))
        closed=[]
        def close(**kw):closed.append(kw);return dict(writer_alive=False,errors=[])
        recorder.close=close
        def temps(*args,**kw):return dict.fromkeys(joints,34),{}
        with tempfile.TemporaryDirectory() as folder, patch('recorder_bus_readonly.read_temperatures',temps):
            phase=run_phase(bus,config,order,{},Path(folder),.04,True,
                recorder_factory=lambda *a:recorder,clock=clock.now,pause=clock.sleep)
        self.assertFalse(phase['completed']);self.assertIn('enqueue failed',phase['error']['reason'])
        self.assertEqual(len(closed),1);self.assertIs(port.writePort,write)
        self.assertTrue(packets);self.assertTrue(all(raw[4]==0x82 for raw in packets))

    def test_recorder_close_exception_is_reported_and_does_not_escape_phase(self):
        # Exercise the same bus fixture, replacing only its recorder cleanup.
        config=load();joints=config['joint_names'];clock=Clock();port=BufferedPort();queue=[]
        order={j:i+1 for i,j in enumerate(joints)}
        def sync(p,address,width,ids,size):
            for mid in ids:
                payload=bytearray(width)
                if address==56:payload[7]=34
                queue.append((packet(mid,0,list(payload)),0))
            return 0
        bus=types.SimpleNamespace(port_handler=port,
            packet_handler=types.SimpleNamespace(syncReadTx=sync,rxPacket=lambda p:queue.pop(0)))
        def failed_close(**kw):raise OSError('disk full')
        recorder=types.SimpleNamespace(capture=lambda *a,**kw:None,close=failed_close)
        with tempfile.TemporaryDirectory() as folder, patch('recorder_bus_readonly.read_temperatures',
                lambda *a,**kw:(dict.fromkeys(joints,34),{})):
            phase=run_phase(bus,config,order,{},Path(folder),.04,True,
                recorder_factory=lambda *a:recorder,clock=clock.now,pause=clock.sleep)
        self.assertFalse(phase['completed'])
        self.assertEqual(phase['cleanup_errors'][0]['operation'],'recorder.close')


if __name__ == '__main__': unittest.main()
