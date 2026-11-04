import ast
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import dry_inference as d
import nov_readonly as n

class Tests(unittest.TestCase):
    def test_shared_stop_prevents_model_or_sdk_imports(self):
        with patch.object(n,'check_stop',side_effect=n.SafetyStop('latched')),patch('builtins.__import__',wraps=__import__) as imp:
            with self.assertRaises(n.SafetyStop):d.main()
        self.assertFalse(any(c.args[0].startswith(('torch','lerobot','serial')) for c in imp.call_args_list))
    def test_no_motor_write_or_robot_connection_calls(self):
        tree=ast.parse(Path(d.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                self.assertNotIn(node.func.attr,{'send_action','sync_write','write','configure','calibrate','enable_torque','disable_torque'})
                if node.func.attr=='connect':self.assertEqual(ast.unparse(node.func.value),'camera')
    def test_mode_and_health_stay_fail_closed(self):
        home=dict(joint_names=list(n.JOINTS),state=[0.]*5+[20.],tolerance_deg=5.)
        value=dict(positions=dict(zip(n.JOINTS,home['state'])),modes=dict.fromkeys(n.JOINTS,0))
        class Bus:
            def read(self,reg,j,**kw):return 200 if reg=='Present_Load' else 0
        session=types.SimpleNamespace(read=lambda h:value,bus=Bus())
        with patch.object(n,'stop_serial',side_effect=n.SafetyStop('load')):
            with self.assertRaises(n.SafetyStop):d.safe_health(session,home)

if __name__=='__main__':unittest.main()
