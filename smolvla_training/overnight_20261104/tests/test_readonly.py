import contextlib
import importlib.util
from pathlib import Path
import tempfile
import types
import hashlib
import sys
import unittest
from unittest.mock import patch
import nov_readonly as n

def packet(i=1,instruction=2,params=(40,1)):
    raw=bytes([255,255,i,len(params)+2,instruction])+bytes(params)
    return raw+bytes([(~sum(raw[2:]))&255])
class Port:
    def __init__(self):self.is_open=False;self.ser=self;self.sent=[]
    def openPort(self):self.is_open=True;return True
    def closePort(self):self.is_open=False
    def close(self):self.is_open=False
    def writePort(self,p):self.sent.append(bytes(p));return len(p)
class Bus:
    def __init__(self):
        self.port_handler=Port();self.calls=[];self.torque={j:0 for j in n.JOINTS};self.temp=30;self.closed=[]
        self.motors={j:types.SimpleNamespace(id=i) for i,j in enumerate(n.JOINTS,1)}
    @property
    def is_connected(self):return self.port_handler.is_open
    def connect(self,handshake):assert handshake is False;self.port_handler.openPort()
    def disconnect(self,disable_torque):self.closed.append(disable_torque);self.port_handler.closePort()
    def read(self,name,j,**kwargs):
        assert kwargs==dict(normalize=False,num_retry=0);self.calls.append((name,j))
        addr,width={'Torque_Enable':(40,1),'Present_Temperature':(63,1),'Operating_Mode':(33,1),'Present_Position':(56,2)}[name]
        self.port_handler.writePort(packet(self.motors[j].id,2,(addr,width)))
        return {'Torque_Enable':self.torque[j],'Present_Temperature':self.temp,'Operating_Mode':0,'Present_Position':20}[name]
    def _normalize(self,values):return {k:float(v) for k,v in values.items()}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/'stop.json')
        p=patch.object(n,'latch_path',lambda:Path(self.path));p.start();self.addCleanup(p.stop)
    def test_normal_snapshot_and_close(self):
        b=Bus()
        with n.ReadSession(b,Port,claim=False) as s:r=s.read()
        self.assertTrue(all(v==0 for v in r['torque'].values()));self.assertEqual(b.closed,[False])
        self.assertEqual(len(b.calls),24);self.assertFalse(b.is_connected)
    def test_first_on_stops_without_further_reads(self):
        b=Bus();b.torque['elbow_flex']=1
        with self.assertRaises(n.SafetyStop),n.ReadSession(b,Port,claim=False) as s:s.read()
        self.assertEqual(b.calls,[('Torque_Enable',j) for j in n.JOINTS[:3]])
        self.assertTrue(Path(self.path).exists());self.assertFalse(b.is_connected)
    def test_hot_stops_after_first_temperature(self):
        b=Bus();b.temp=45
        with self.assertRaises(n.SafetyStop),n.ReadSession(b,Port,claim=False) as s:s.read()
        self.assertEqual(len(b.calls),7);self.assertFalse(b.is_connected)
    def test_shared_stop_prevents_open(self):
        Path(self.path).touch();b=Bus()
        with self.assertRaises(n.SafetyStop),n.ReadSession(b,Port,claim=False):pass
        self.assertFalse(b.is_connected);self.assertEqual(b.calls,[])
    def test_write_and_sync_read_blocked(self):
        g=n.PacketGuard()
        for frame in (packet(instruction=3,params=(40,0)),packet(instruction=3,params=(40,1)),
                      packet(instruction=3,params=(42,0,0)),packet(i=254,instruction=130,params=(56,2,1,2,3,4,5,6))):
            with self.assertRaises(n.ForbiddenCommand):g.inspect(frame)
    def test_allowed_read_and_bad_checksum(self):
        g=n.PacketGuard();g.inspect(packet());bad=bytearray(packet());bad[-1]^=1
        with self.assertRaises(n.ForbiddenCommand):g.inspect(bad)
        self.assertEqual(g.read_packets,1)
    def test_unknown_register_read_refused(self):
        with self.assertRaises(n.ForbiddenCommand):n.PacketGuard().inspect(packet(params=(5,1)))
    def test_nan_normalization_fails_closed(self):
        b=Bus();b._normalize=lambda v:{k:float('nan') for k in v}
        with self.assertRaises(n.SafetyStop),n.ReadSession(b,Port,claim=False) as s:s.read()
        self.assertFalse(b.is_connected)
    def test_home_gate_keeps_five_degree_limit(self):
        r={'positions':dict.fromkeys(n.JOINTS,0.)};home=dict(joint_names=list(n.JOINTS),state=[0.]*6,tolerance_deg=5.)
        n.home_gate(r,home);r['positions']['elbow_flex']=5.01
        with self.assertRaises(n.SafetyStop):n.home_gate(r,home)
    def test_close_failure_uses_raw_close(self):
        b=Bus()
        def bad(**kw):raise OSError('mock close')
        b.disconnect=bad
        with self.assertRaises(OSError),n.ReadSession(b,Port,claim=False) as s:s.read()
        self.assertFalse(b.is_connected)
    def test_guard_restored_after_error(self):
        original=Port.writePort;b=Bus();b.temp=45
        with self.assertRaises(n.SafetyStop),n.ReadSession(b,Port,claim=False) as s:s.read()
        self.assertIs(Port.writePort,original)
    def test_no_duplicate_normalization(self):
        b=Bus();calls=[]
        def normal(v):calls.append(v);return v
        b._normalize=normal
        self.assertEqual(n.normalize_position(b,'shoulder_pan',10),10)
        self.assertEqual(calls,[{1:10}])
    def test_robot_owner_kept_alive_until_port_closed(self):
        class Robot:
            destroyed=[]
            def __init__(self,config):self.bus=Bus()
            def __del__(self):
                self.destroyed.append(self.bus.is_connected)
                if self.bus.is_connected:self.bus.disconnect(disable_torque=False)
        sdk=types.ModuleType('scservo_sdk');sdk.PortHandler=Port
        robotmod=types.ModuleType('lerobot.robots.so_follower')
        robotmod.SO101Follower=Robot;robotmod.SO101FollowerConfig=lambda **kw:kw
        modules={'scservo_sdk':sdk,'lerobot':types.ModuleType('lerobot'),
                 'lerobot.robots':types.ModuleType('lerobot.robots'),'lerobot.robots.so_follower':robotmod}
        with patch.dict(sys.modules,modules),patch.object(Path,'read_bytes',return_value=b'safe'),patch.object(n,'CALIBRATION_HASH',hashlib.sha256(b'safe').hexdigest()):
            with n.ReadSession(claim=False) as session:
                self.assertTrue(session.bus.is_connected);self.assertEqual(Robot.destroyed,[])
                session.read()
            self.assertFalse(session.bus.is_connected)

if __name__=='__main__':unittest.main()
