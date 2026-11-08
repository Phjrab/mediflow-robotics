import contextlib
import unittest
from types import SimpleNamespace
from first_trial_core import JOINTS,Hold
from goal_telemetry import GoalTelemetry,decode_goal
from trial_runtime import DeviceIO

def packet():
    data=[255,255,254,22,131,42,2]
    for i in range(1,7):data.extend([i,100+i,0])
    return bytes(data+[(~sum(data[2:]))&255])

def normalize(raw):return {i:float(v)/100 for i,v in raw.items()}

class Tests(unittest.TestCase):
    def test_packet_decode_checksum_and_duplicate_id(self):
        self.assertEqual(decode_goal(packet()),{i:100+i for i in range(1,7)})
        bad=bytearray(packet());bad[8]^=1
        with self.assertRaises(Hold):decode_goal(bad)
        bad=bytearray(packet());bad[10]=1;bad[-1]=(~sum(bad[2:-1]))&255
        with self.assertRaises(Hold):decode_goal(bad)
    def test_separate_sdk_clip_and_integer_values(self):
        t=GoalTelemetry();t.begin([1.5]*6);t.capture(packet(),normalize)
        row=t.finish({name+'.pos':1.1 for name in JOINTS})
        self.assertEqual(row['status'],'SDK_RETURNED');self.assertEqual(row['wire_raw'][JOINTS[0]],101)
        self.assertAlmostEqual(row['sdk_minus_requested'][0],-.4);self.assertAlmostEqual(row['wire_minus_sdk'][0],-.09)
        self.assertEqual(row['motor_acknowledgement'],'UNVERIFIED')
    def test_unscoped_duplicate_and_missing_capture_rejected(self):
        t=GoalTelemetry()
        with self.assertRaises(Hold):t.capture(packet(),normalize)
        t.begin([0.]*6)
        with self.assertRaises(Hold):t.finish({name+'.pos':0. for name in JOINTS})
        t.capture(packet(),normalize)
        with self.assertRaises(Hold):t.capture(packet(),normalize)
    def test_invalid_sdk_return_preserves_error(self):
        for value in (None,{},dict.fromkeys([j+'.pos' for j in JOINTS],float('nan'))):
            t=GoalTelemetry();t.begin([0.]*6);t.capture(packet(),normalize)
            try:t.finish(value)
            except Hold as e:t.fail(e)
            self.assertEqual(t.rows[-1]['status'],'ERROR');self.assertIsNone(t.active)
    def device(self,fail=False):
        io=DeviceIO({},expected_torque=1,allow_goal=True);io.authorized=True;calls=[]
        @contextlib.contextmanager
        def once():calls.append('permission');yield
        io.permission=SimpleNamespace(goal_once=once)
        def send(action):
            calls.append(dict(action));io.goal_telemetry.capture(packet(),normalize)
            if fail:raise OSError('fixture')
            return dict(action)
        io.robot=SimpleNamespace(send_action=send)
        return io,calls
    def test_device_calls_only_existing_send_once(self):
        io,calls=self.device();row=io.send_target([1.]*6)
        self.assertEqual(len(calls),2);self.assertEqual(row['status'],'SDK_RETURNED');self.assertEqual(len(io.goal_telemetry.rows),1)
    def test_device_exception_is_not_retried(self):
        io,calls=self.device(True)
        with self.assertRaises(OSError):io.send_target([1.]*6)
        self.assertEqual(len(calls),2);self.assertEqual(io.goal_telemetry.rows[0]['status'],'ERROR')
    def test_unauthorized_no_call_no_capture(self):
        io,calls=self.device();io.authorized=False
        with self.assertRaises(Hold):io.send_target([1.]*6)
        self.assertEqual(calls,[]);self.assertEqual(io.goal_telemetry.rows,[])

if __name__=='__main__':unittest.main()
