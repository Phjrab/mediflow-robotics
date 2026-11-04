import ast
import json
from pathlib import Path
import time
import unittest
import home_align_web as web
import nov_readonly as n

class Tests(unittest.TestCase):
    def test_cache_has_only_cached_values(self):
        c=web.Cache();state=dict(positions=dict.fromkeys(n.JOINTS,0.),torque=dict.fromkeys(n.JOINTS,0),temperatures=dict.fromkeys(n.JOINTS,30))
        c.put(state,dict(state=[0.]*6));value=c.get();self.assertEqual(len(value['rows']),6)
        self.assertTrue(all(v['torque']==0 for v in value['rows']))
    def test_stale_invalidates_previous_rows(self):
        c=web.Cache();c.value={'rows':[1]};c.updated=time.monotonic()-2
        self.assertIn('error',c.get());self.assertNotIn('rows',c.get())
    def test_no_motor_write_or_robot_connect_calls(self):
        source=Path(web.__file__).read_text(encoding='utf-8');tree=ast.parse(source)
        names={'send_action','sync_write','enable_torque','disable_torque','configure','calibrate','connect'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):self.assertNotIn(node.func.attr,names)

if __name__=='__main__':unittest.main()
