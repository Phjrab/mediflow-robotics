import ast
import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest

import live_safety_runtime as safe
import probe_current_state as probe

ROOT = Path(__file__).resolve().parent

def packet(motor_id, instruction, parameters):
    body = bytes((motor_id, len(parameters)+2, instruction, *parameters))
    return b"\xff\xff" + body + bytes((255-sum(body)%256,))

def goal():
    params = [42, 2]
    for motor_id in range(1, 7):
        params += [motor_id, 0, 8]
    return packet(254, 0x83, params)

class FakePort:
    def __init__(self):
        self.sent = []
        self.ser = types.SimpleNamespace(is_open=True)
        def close():
            self.ser.is_open = False
        self.ser.close = close
    def writePort(self, data):
        self.sent.append(bytes(data))
        return len(data)

class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.port = FakePort()
        self.guard = safe.PacketPermission(self.port, allow_goal=True).install()
    def tearDown(self):
        self.guard.restore()
    def test_individual_and_sync_read(self):
        self.port.writePort(packet(1, 2, [40, 1]))
        self.port.writePort(packet(254, 0x82, [56, 2, 1, 2, 3, 4, 5, 6]))
        self.assertEqual(self.guard.read_packets, 2)
        self.assertEqual(self.guard.goal_packets, 0)
    def test_no_implicit_goal(self):
        with self.assertRaises(safe.Hold):self.port.writePort(goal())
        self.assertEqual(self.port.sent, [])
    def test_one_explicit_goal_only(self):
        with self.guard.goal_once():
            self.port.writePort(goal())
            with self.assertRaises(safe.Hold):self.port.writePort(goal())
        self.assertEqual(self.guard.goal_packets, 1)
        self.assertEqual(len(self.port.sent), 1)
    def test_torque_mode_and_calibration_writes_always_blocked(self):
        for register in (40, 33, 9, 11, 31, 55):
            with self.assertRaises(safe.Hold):
                with self.guard.goal_once():self.port.writePort(packet(1, 3, [register, 1]))
        self.assertEqual(self.port.sent, [])
    def test_read_only_profile_cannot_arm(self):
        self.guard.restore()
        self.guard = safe.PacketPermission(self.port).install()
        with self.assertRaises(safe.Hold):
            with self.guard.goal_once():pass
    def test_thread_without_serial_ownership_is_blocked(self):
        errors = []
        def tx():
            try:self.port.writePort(packet(1, 2, [40, 1]))
            except safe.Hold:errors.append(True)
        t = threading.Thread(target=tx);t.start();t.join()
        self.assertEqual(errors, [True]);self.assertEqual(self.port.sent, [])
    def test_corrupted_or_duplicate_id_packet_blocked(self):
        for p in (goal()[:-1]+b"\x00", packet(254, 0x82, [56, 2, 1, 1])):
            with self.assertRaises(safe.Hold):self.port.writePort(p)
        self.assertEqual(self.port.sent, [])
    def test_other_port_blocked_and_sealed_blocks_reads(self):
        with self.assertRaises(safe.Hold):FakePort().writePort(packet(1, 2, [40, 1]))
        self.guard.seal()
        with self.assertRaises(safe.Hold):self.port.writePort(packet(1, 2, [40, 1]))
        self.assertEqual(self.port.sent, [])

class MonitoringTests(unittest.TestCase):
    def test_monitor_runs_on_owner_while_prediction_runs_elsewhere(self):
        owner = threading.get_ident();calls=[];workers=[]
        def predict():
            workers.append(threading.get_ident());time.sleep(.04);return 7
        value = safe.monitored_inference(predict,lambda:calls.append(threading.get_ident()),
                    deadline=time.monotonic()+1,timeout=.5,poll=.005)
        self.assertEqual(value,7);self.assertGreater(len(calls),2)
        self.assertEqual(set(calls),{owner});self.assertNotEqual(workers[0],owner)
    def test_temperature_load_torque_and_stop_abort_result(self):
        for reason in ("55도", "Overload", "토크OFF", "중단 기록", "카메라 오류"):
            calls=[];release=threading.Event();cancel=threading.Event()
            def watch():
                calls.append(1)
                if len(calls)>1:raise safe.Hold(reason)
            try:
                with self.assertRaisesRegex(safe.Hold,reason):
                    safe.monitored_inference(lambda:release.wait(.2),watch,
                        deadline=time.monotonic()+1,timeout=.5,poll=.005,cancelled=cancel)
                self.assertTrue(cancel.is_set())
            finally:release.set()
    def test_timeout_discards_late_prediction(self):
        release=threading.Event();cancel=threading.Event()
        try:
            with self.assertRaises(safe.Hold):
                safe.monitored_inference(lambda:release.wait(.2),lambda:None,
                    deadline=time.monotonic()+1,timeout=.015,poll=.005,cancelled=cancel)
            self.assertTrue(cancel.is_set())
        finally:release.set()
    def test_keyboard_interrupt_and_prediction_failure(self):
        for error in (KeyboardInterrupt(), ValueError("NaN")):
            cancel=threading.Event()
            def fail():raise error
            with self.assertRaises(type(error)):
                safe.monitored_inference(fail,lambda:None,deadline=time.monotonic()+1,timeout=.5,cancelled=cancel)
            self.assertTrue(cancel.is_set())
    def test_expired_deadline_does_not_start_prediction(self):
        predictions=[]
        with self.assertRaises(safe.Hold):
            safe.monitored_inference(lambda:predictions.append(1),lambda:None,
                deadline=time.monotonic()-1,timeout=.5)
        self.assertEqual(predictions,[])
    def test_slow_health_read_refuses_before_predict(self):
        predictions=[]
        with self.assertRaises(safe.Hold):
            safe.monitored_inference(lambda:predictions.append(1),lambda:time.sleep(.015),
                deadline=time.monotonic()+1,timeout=.5,poll=.001,max_monitor_gap=.005)
        self.assertEqual(predictions,[])

class CleanupTests(unittest.TestCase):
    def test_cleanup_has_no_motor_write_and_port_precedes_camera(self):
        events=[];port=FakePort();guard=safe.PacketPermission(port,allow_goal=True).install()
        def disconnect(**kw):
            self.assertEqual(kw,{"disable_torque":False});events.append("port");port.ser.close()
        robot=types.SimpleNamespace(bus=types.SimpleNamespace(port_handler=port,disconnect=disconnect))
        cam=types.SimpleNamespace(disconnect=lambda:events.append("camera"))
        errors=safe.close_without_writes(robot,[cam],True,guard)
        self.assertEqual(errors,[]);self.assertEqual(events,["port","camera"])
        self.assertEqual(port.sent,[]);self.assertFalse(port.ser.is_open)
    def test_disconnect_failure_uses_only_close_fallback(self):
        port=FakePort()
        def fail(**kw):raise RuntimeError("통신 끊김")
        robot=types.SimpleNamespace(bus=types.SimpleNamespace(port_handler=port,disconnect=fail))
        errors=safe.close_without_writes(robot,[],True)
        self.assertTrue(errors);self.assertFalse(port.ser.is_open);self.assertEqual(port.sent,[])

class ProbeTests(unittest.TestCase):
    def make_bus(self, torque=0, temperature=30):
        calls=[]
        def read(register,joint,**kw):
            calls.append((register,joint));self.assertFalse(kw["normalize"])
            self.assertEqual(kw["num_retry"],0)
            return {"Torque_Enable":torque,"Present_Temperature":temperature,
                    "Operating_Mode":0,"Present_Position":1}[register]
        bus=types.SimpleNamespace(read=read,motors={j:types.SimpleNamespace(id=i+1) for i,j in enumerate(probe.JOINTS)},_normalize=lambda v:v)
        return bus,calls
    def test_on_stops_after_first_torque_read(self):
        bus,calls=self.make_bus(torque=1);result={}
        with self.assertRaises(RuntimeError):probe.read_values(bus,None,result)
        self.assertEqual(calls,[("Torque_Enable","shoulder_pan")])
    def test_45_degree_stops_before_mode_or_position(self):
        bus,calls=self.make_bus(temperature=45);result={}
        with self.assertRaises(RuntimeError):probe.read_values(bus,None,result)
        self.assertEqual(len(calls),7);self.assertEqual(calls[-1][0],"Present_Temperature")
    def test_all_off_reads_state_without_write(self):
        bus,calls=self.make_bus();result={}
        probe.read_values(bus,{"state":[0]*6},result)
        self.assertEqual(len(result["positions"]),6);self.assertEqual(len(calls),24)
        self.assertEqual(result["home_delta"],dict.fromkeys(probe.JOINTS,1.0))
    def test_stop_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/"SERIAL_STOP.json";stop.write_bytes(b'{"stop":true}')
            before=stop.read_bytes()
            with self.assertRaises(safe.Hold):safe.check_stop_record(directory)
            self.assertEqual(stop.read_bytes(),before)
    def test_renamed_stop_file_does_not_implicitly_rearm(self):
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/"SERIAL_STOP.cleared_20261105_185629.json"
            stop.write_bytes(b'{"stop":true}')
            before=stop.read_bytes()
            with self.assertRaises(safe.Hold):safe.check_stop_record(directory)
            self.assertEqual(stop.read_bytes(),before)

class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location("runner_v6",ROOT/"so101_first_action_test_safe_v6.py")
        cls.runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.runner)
    def test_original_numeric_gates_preserved(self):
        with self.assertRaises(ValueError):self.runner.vector([float("nan")]*6)
        with self.assertRaises(ValueError):self.runner.vector([0]*5)
        with self.assertRaises(ValueError):self.runner.vector([0]*5+[101])
        with self.assertRaises(ValueError):self.runner.rate_config(30,2,20)
        self.runner.rate_config(30,.5,20)
    def test_original_health_and_partial_on_gates_preserved(self):
        j=self.runner.JOINTS
        with self.assertRaises(ValueError):self.runner.health_gate(dict.fromkeys(j,55),dict.fromkeys(j,0),dict.fromkeys(j,0))
        with self.assertRaises(ValueError):self.runner.health_gate(dict.fromkeys(j,50),dict.fromkeys(j,0),dict.fromkeys(j,0),starting=True)
        with self.assertRaises(ValueError):self.runner.health_gate(dict.fromkeys(j,30),dict.fromkeys(j,201),dict.fromkeys(j,0))
        with self.assertRaises(RuntimeError):self.runner.motor_mode_gate(dict.fromkeys(j,0),dict.fromkeys(j,0),True)
    def test_no_blind_hold_function_or_configure_call(self):
        tree=ast.parse((ROOT/"so101_first_action_test_safe_v6.py").read_text(encoding="utf-8"))
        self.assertNotIn("hold_measured_position",[n.name for n in tree.body if isinstance(n,ast.FunctionDef)])
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ("configure","calibrate","sync_write") for n in ast.walk(tree)))
        self.assertIn("with permission.goal_once():",(ROOT/"so101_first_action_test_safe_v6.py").read_text(encoding="utf-8"))
    def test_python_310_syntax(self):
        for path in ROOT.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"),feature_version=(3,10))

if __name__=="__main__":unittest.main()
