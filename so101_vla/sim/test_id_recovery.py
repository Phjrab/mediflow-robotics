"""Late SYNC_READ packet regression, through production receive/retry code."""
import types
import unittest

import direct_live as base
from communication_retry import CommunicationHold, CommunicationPolicy, confirmed_hold
from config_runtime import load
from individual_reply_guard import StrictIndividualReplies
from test_communication_retry import Clock, JOINTS, Port, reply


class BufferedPort(Port):
    def __init__(self):
        self.pending=bytearray()
        self.resets=0
        self.ser=types.SimpleNamespace(reset_input_buffer=self.reset)

    def reset(self):
        self.resets+=1
        self.pending.clear()

    def getBytesAvailable(self):return len(self.pending)

    def readPort(self,n):
        data=bytes(self.pending[:n]);del self.pending[:n]
        return data


class IdRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.log=[];self.port=BufferedPort()
        self.policy=CommunicationPolicy(JOINTS,load()['communication'],self.log.append,
            clock=self.clock.now,pause=self.clock.sleep)

    def test_fresh_individual_read_discards_foreign_response_and_reissues_exact_request(self):
        requests=[];reset_counts=[]
        def tx(port,request):
            requests.append(list(request));reset_counts.append(port.resets)
            return (reply(1 if len(requests)==1 else 2,value=37),0,0)
        handler=types.SimpleNamespace(txRxPacket=tx)
        request=[255,255,2,4,2,63,1,0]
        with StrictIndividualReplies(handler,retry=self.policy) as guard:
            received=handler.txRxPacket(self.port,request)
            self.assertEqual(received[0][2],2)
            self.assertIsNone(guard.fault)
        self.assertEqual(requests,[request,request])
        self.assertGreater(reset_counts[0],0)
        self.assertGreater(reset_counts[1],reset_counts[0])
        self.assertEqual(self.policy.summary()['id_mismatch_by_joint']['shoulder_lift'],1)
        self.assertEqual(self.policy.counts['shoulder_lift']['-7'],0)

    def test_1935_late_id1_abandons_shifted_batch_and_refreshes_missing_axes(self):
        order=[1,2,3,4,6,5];ids={JOINTS[i-1]:i for i in order}
        rx=[];fresh=[]
        def receive(port):
            rx.append(1);self.clock.sleep(.01)
            if len(rx)==1:return b'',-6
            port.pending.extend(reply(2)+reply(3)+reply(4)+reply(6))
            return reply(1,value=201),0
        def individual(port,request):
            self.assertEqual(port.getBytesAvailable(),0)
            fresh.append(request[2])
            return reply(request[2],value=request[2]),0,0
        handler=types.SimpleNamespace(rxPacket=receive,txRxPacket=individual)
        values=base.read_sync_replies(handler,self.port,ids,33,1,self.policy)
        self.assertEqual({j:b[0] for j,b in values.items()},dict(zip(JOINTS,range(1,7))))
        self.assertEqual(len(rx),2)
        self.assertEqual(fresh,order)
        self.assertEqual(self.policy.counts['shoulder_pan']['-6'],1)
        self.assertEqual(self.policy.counts['shoulder_lift']['id_mismatch'],1)
        self.assertGreater(self.policy.discarded_rx_bytes,0)

    def test_healthy_sync_does_not_clear_between_axis_replies(self):
        packets=iter((reply(i,value=i),0) for i in range(1,7))
        handler=types.SimpleNamespace(rxPacket=lambda p:next(packets))
        reset_counts=[]
        def request():
            reset_counts.append(self.port.resets)
            return base.read_sync_replies(handler,self.port,dict(zip(JOINTS,range(1,7))),33,1,self.policy)
        values=self.policy.perform(list(range(1,7)),'read:33:1',request,port=self.port)
        self.assertEqual(self.port.resets,1)
        self.assertEqual(reset_counts,[1])
        self.assertEqual(values['gripper'],b'\x06')

    def test_five_foreign_responses_hold_without_returning_foreign_state(self):
        calls=[]
        def tx(port,request):
            calls.append(1);self.clock.sleep(.001)
            return reply(1),0,0
        handler=types.SimpleNamespace(txRxPacket=tx)
        with StrictIndividualReplies(handler,retry=self.policy):
            with self.assertRaisesRegex(CommunicationHold,'5회'):
                handler.txRxPacket(self.port,[255,255,2,4,2,63,1,0])
        self.assertEqual(len(calls),5)
        self.assertEqual(self.policy.summary()['id_mismatch_by_joint']['shoulder_lift'],5)

    def test_sdk_minus7_foreign_packet_counts_both_without_accepting_it(self):
        with self.assertRaises(base.IdMismatchReply) as caught:
            base.parse_reply(reply(1,value=99),-7,2,1)
        self.assertEqual(caught.exception.observed['kind'],'id_mismatch')
        calls=[]
        def tx(port,request):
            calls.append(1)
            return (reply(1,value=99),-7,0) if len(calls)==1 else (reply(2,value=34),0,0)
        handler=types.SimpleNamespace(txRxPacket=tx)
        with StrictIndividualReplies(handler,retry=self.policy):
            value=handler.txRxPacket(self.port,[255,255,2,4,2,63,1,0])
        self.assertEqual(value[0][5],34)
        self.assertEqual(self.policy.counts['shoulder_lift']['id_mismatch'],1)
        self.assertEqual(self.policy.counts['shoulder_lift']['-7'],1)

    def test_id_recovery_does_not_extend_two_hundred_ms_watchdog(self):
        self.policy.activate();self.clock.sleep(.19)
        handler=types.SimpleNamespace(txRxPacket=lambda p,r:(self.clock.sleep(.011) or reply(1),0,0))
        with StrictIndividualReplies(handler,retry=self.policy):
            with self.assertRaisesRegex(CommunicationHold,'200ms'):
                handler.txRxPacket(self.port,[255,255,2,4,2,63,1,0])

    def test_temperature_pacing_flushes_bytes_that_arrive_during_wait(self):
        sent=[]
        def pause(delay):
            self.clock.sleep(delay)
            if delay>=.015:self.port.pending.extend(reply(1,value=99))
        self.policy.pause=pause
        self.policy.last_temp_request[2]=self.clock.now()
        def tx(port,request):
            self.assertEqual(port.getBytesAvailable(),0)
            sent.append(self.clock.now())
            return reply(2,value=34),0,0
        handler=types.SimpleNamespace(txRxPacket=tx)
        with StrictIndividualReplies(handler,retry=self.policy):
            result=handler.txRxPacket(self.port,[255,255,2,4,2,63,1,0])
        self.assertGreaterEqual(sent[0],.02)
        self.assertGreater(self.policy.discarded_rx_bytes,0)
        self.assertEqual(result[0][5],34)

    def test_hold_resets_input_before_position_hold_write_and_at_most_five_attempts(self):
        self.policy.attach(self.port)
        writes=[];raw=dict.fromkeys(JOINTS,2000)
        def feedback():
            self.port.pending.extend(b'old_status')
            return {'present_raw':raw}
        def send(target):
            self.assertEqual(self.port.getBytesAvailable(),0)
            writes.append(dict(target))
        io=types.SimpleNamespace(arm_bounds={j:(0,4095) for j in JOINTS},wire_fault=False,
            feedback=feedback,_send_once=send,pause=self.clock.sleep,
            read_block=lambda a,w:{j:(2001).to_bytes(2,'little') for j in JOINTS})
        result=confirmed_hold(io,self.policy,JOINTS)
        self.assertEqual(result['status'],'HOLD_NOT_CONFIRMED')
        self.assertEqual(len(writes),5)
        self.assertGreaterEqual(self.port.resets,5)

    def test_bad_checksum_and_real_servo_status_still_fail_closed(self):
        broken=bytearray(reply(2));broken[-1]^=1
        with self.assertRaises(base.Stop):base.parse_reply(broken,0,2,1)
        status=bytearray(reply(2));status[4]=32;status[-1]=(~sum(status[2:-1]))&255
        with self.assertRaises(base.ServoStatusStop):base.parse_reply(status,0,2,1)


if __name__=='__main__':unittest.main()
