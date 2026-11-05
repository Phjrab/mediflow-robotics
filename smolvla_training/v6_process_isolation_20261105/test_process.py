import ast
import os
from pathlib import Path
import unittest
from latency_dependencies import prepare
prepare()
from process_policy import Transport,deny_hardware
from mock_engine import factory,slow_factory
from build_fast_variant import render

class ProcessTests(unittest.TestCase):
    def test_other_process_echo_and_clean_close(self):
        transport=Transport(factory,{})
        try:
            self.assertNotEqual(transport.pid,os.getpid())
            self.assertEqual(transport.call("echo",[[.1]*6 for _ in range(50)]),[[.1]*6 for _ in range(50)])
        finally:transport.close()
        self.assertFalse(transport.process.is_alive())
        self.assertTrue(transport.history)
    def test_child_error_propagates_and_stops(self):
        transport=Transport(factory,{})
        try:
            with self.assertRaisesRegex(RuntimeError,"합성 정책 오류"):transport.call("fail")
        finally:transport.close()
        self.assertFalse(transport.process.is_alive())
    def test_child_hardware_open_denied_before_os(self):
        transport=Transport(factory,{})
        try:
            with self.assertRaisesRegex(RuntimeError,"장치 접근 금지"):transport.call("hardware")
        finally:transport.close()
        self.assertFalse(transport.process.is_alive())
    def test_concurrent_and_closed_calls_refused(self):
        transport=Transport(factory,{})
        try:
            transport.lock.acquire()
            with self.assertRaises(RuntimeError):transport.call("echo")
            transport.lock.release()
        finally:transport.close()
        with self.assertRaises(RuntimeError):transport.call("echo")
    def test_load_timeout_terminates_child(self):
        with self.assertRaises(TimeoutError):Transport(slow_factory,{},timeout=.01)
    def test_device_paths_denied_gpu_path_allowed(self):
        for p in ("/dev/ttyACM0","/dev/video8","/dev/v4l/by-id/c","/dev/serial/by-id/c"):
            with self.assertRaises(RuntimeError):deny_hardware("open",(p,))
        deny_hardware("open",("/dev/nvhost-gpu",))
    def test_unchanged_gates_main_and_cleanup(self):
        source=(prepare()/"so101_first_action_test_safe_v6_dry.py").read_text(encoding="utf-8")
        target=render(source)
        for name in ("motor_mode_gate","health_gate","home_gate","first_action_gate","close_session","safe_target","verify_units"):
            def node(s):return ast.dump(next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
            self.assertEqual(node(source),node(target))
        self.assertIn("timeout=50.0 / args.fps",target)
        self.assertIn("allow_goal=False",target)
    def test_python310_syntax(self):
        for p in Path(__file__).parent.glob("*.py"):ast.parse(p.read_text(encoding="utf-8"),feature_version=(3,10))

if __name__=="__main__":unittest.main()
