"""Exercise transport recovery and the real StreamingIO dispatch without hardware."""
import sys
import types
import unittest
import contextlib

import direct_live as base
from config_runtime import load
from communication_retry import CommunicationPolicy,CommunicationHold,RetryableWireError,confirmed_hold
from individual_reply_guard import StrictIndividualReplies

JOINTS=load()['joint_names']

@contextlib.contextmanager
def fake_imports(modules,base_class):
    previous={name:sys.modules.get(name) for name in modules}
    original=base.device_class
    sys.modules.update(modules);base.device_class=lambda:base_class
    try:yield
    finally:
        base.device_class=original
        for name,module in previous.items():
            if module is None:sys.modules.pop(name,None)
            else:sys.modules[name]=module

class Clock:
    def __init__(self):self.t=0.
    def now(self):return self.t
    def sleep(self,dt):self.t+=dt

class Port:
    is_using=False
    def isPacketTimeout(self):return False
    def getBytesAvailable(self):return 0
    def writePort(self,packet):return len(packet)

def reply(mid,value=8,width=1):
    body=bytes((255,255,mid,width+2,0))+int(value).to_bytes(width,'little')
    return body+bytes(((~sum(body[2:]))&255,))

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.rows=[];self.port=Port()
        self.policy=CommunicationPolicy(JOINTS,load()['communication'],self.rows.append,
            clock=self.clock.now,pause=self.clock.sleep)

    def test_transient_success_and_four_failures_defer_fifth_holds(self):
        for failures in (1,4,5):
            self.setUp();calls=[]
            def call():
                calls.append(1);self.clock.sleep(.001)
                if len(calls)<=failures:raise RetryableWireError('corrupt',{'communication':-7})
                return 8
            if failures==5:
                with self.assertRaises(CommunicationHold):self.policy.perform([5],'read:33:38',call,port=self.port)
                self.assertEqual(len(calls),5)
            else:self.assertEqual(self.policy.perform([5],'read:33:38',call,port=self.port),8)
            self.assertEqual(self.policy.counts['wrist_roll']['-7'],failures)
            if failures>=4:self.assertTrue(any(r['event']=='communication_cycle_deferred' for r in self.rows))

    def test_watchdog_is_per_axis_and_tx_does_not_claim_response(self):
        self.policy.activate();self.clock.sleep(.199)
        self.policy.good(1,'write:42:2',response=False)
        self.policy.good(2,'read:63:1')
        self.clock.sleep(.002)
        with self.assertRaisesRegex(CommunicationHold,'200ms'):self.policy.perform([1],'read:33:38',lambda:1)

    def test_nonretry_error_is_not_retried(self):
        calls=[]
        def call():calls.append(1);raise ValueError('status error')
        with self.assertRaises(ValueError):self.policy.perform([1],'read',call)
        self.assertEqual(len(calls),1)

    def test_temperature_retries_have_twenty_ms_spacing(self):
        times=[]
        def call():
            times.append(self.clock.now())
            if len(times)<3:raise RetryableWireError('timeout',{'communication':-6})
            return 34
        self.policy.perform([6],'read:63:1',call,port=self.port)
        self.assertTrue(all(b-a>=.02-1e-9 for a,b in zip(times,times[1:])))

    def test_sync_drains_all_six_and_retries_only_failed_id(self):
        reads=[];queue=iter((reply(i),-7 if i==5 else 0) for i in range(1,7))
        handler=types.SimpleNamespace(rxPacket=lambda p:next(queue),
            txRxPacket=lambda p,r:(reads.append(r[2]) or reply(r[2]),0,0))
        result=base.read_sync_replies(handler,self.port,dict(zip(JOINTS,range(1,7))),33,1,self.policy)
        self.assertEqual(result,dict.fromkeys(JOINTS,b'\x08'));self.assertEqual(reads,[5])
        self.assertEqual(self.policy.rx_count,7)

    def test_reordered_sync_read_preserves_axis_identity(self):
        order=[1,2,3,4,6,5];ids={JOINTS[i-1]:i for i in order}
        packets=iter((reply(i,value=i),0) for i in order)
        handler=types.SimpleNamespace(rxPacket=lambda p:next(packets))
        got=base.read_sync_replies(handler,self.port,ids,33,1,self.policy)
        self.assertEqual({j:v[0] for j,v in got.items()},{j:i+1 for i,j in enumerate(JOINTS)})

    def test_individual_transient_does_not_latch_fault(self):
        calls=[]
        def tx(port,request):
            calls.append(1)
            return (b'',-6,0) if len(calls)==1 else (reply(1),0,0)
        handler=types.SimpleNamespace(txRxPacket=tx)
        with StrictIndividualReplies(handler,retry=self.policy) as strict:
            got=handler.txRxPacket(self.port,[255,255,1,4,2,63,1,0])
            self.assertEqual(got[1],0);self.assertIsNone(strict.fault)
            self.assertEqual(len(strict.history),2)

    def test_confirmed_hold_readback_and_at_most_five_writes(self):
        for mismatch in (False,True):
            writes=[];raw=dict.fromkeys(JOINTS,2000)
            io=types.SimpleNamespace(arm_bounds={j:(0,4095) for j in JOINTS},wire_fault=False,
                feedback=lambda:{'present_raw':raw},_send_once=lambda target:writes.append(target.copy()),
                read_block=lambda a,w:{j:(2001 if mismatch else 2000).to_bytes(2,'little') for j in JOINTS},pause=self.clock.sleep)
            result=confirmed_hold(io,self.policy,JOINTS)
            self.assertEqual(result['status'],'HOLD_NOT_CONFIRMED' if mismatch else 'HOLD_CONFIRMED')
            self.assertEqual(len(writes),5 if mismatch else 1)

    def test_streaming_io_real_method_dispatch_and_narrow_packet_layout(self):
        import stream_live
        class FakeBase:
            def read_block(self,address,width):
                self.request=(address,width)
                return {j:bytes(range(width)) for j in JOINTS}
        mods={name:types.ModuleType(name) for name in (
            'lerobot','lerobot.cameras','lerobot.cameras.opencv','lerobot.cameras.opencv.camera_opencv')}
        mods['lerobot.cameras.opencv'].OpenCVCameraConfig=object
        mods['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera=object
        with fake_imports(mods,FakeBase):
            cls=stream_live.device_class();io=cls.__new__(cls)
            io.clock=self.clock.now;io._send_policy=lambda raw:('new_send',raw)
            io._hold_policy=lambda:{'status':'HOLD_CONFIRMED'}
            self.assertEqual(io.send({'a':1}),('new_send',{'a':1}))
            self.assertEqual(io.hold()['status'],'HOLD_CONFIRMED')
            result=io.read_block(56,15)
            self.assertEqual(io.request,(56,15))
            self.assertEqual(result['elbow_flex'],bytes(range(15)))
            io.read_block(33,8)
            self.assertEqual(io.mode_cache['wrist_roll'],bytes(range(8)))

    def test_real_streaming_send_retries_same_absolute_goal_without_hard_latch(self):
        import stream_live
        mods={name:types.ModuleType(name) for name in (
            'lerobot','lerobot.cameras','lerobot.cameras.opencv','lerobot.cameras.opencv.camera_opencv')}
        mods['lerobot.cameras.opencv'].OpenCVCameraConfig=object
        mods['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera=object
        with fake_imports(mods,object):
            cls=stream_live.device_class();io=cls.__new__(cls);calls=[]
            raw=dict.fromkeys(JOINTS,2000)
            def write(register,values,**kwargs):
                calls.append((register,values.copy()))
                if len(calls)==1:raise RetryableWireError('corrupt',{'communication':-7})
            io.wire_fault=False;io.motion_ready=True;io.gripper_only=False
            io.ids=dict(zip(JOINTS,range(1,7)));io.raw_bounds={j:(0,4095) for j in JOINTS}
            io.communication=self.policy;io.bus=types.SimpleNamespace(port_handler=self.port,sync_write=write)
            io.permission=types.SimpleNamespace(goal_once=contextlib.nullcontext)
            io.send(raw)
            self.assertEqual(calls,[('Goal_Position',raw),('Goal_Position',raw)])
            self.assertFalse(io.wire_fault);self.assertTrue(io.communication.active)

if __name__=='__main__':unittest.main()
