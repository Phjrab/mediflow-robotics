import ast
from pathlib import Path
import unittest
from decimal import Decimal
import evaluate_offline as e
import prepare_samples as p

class Tests(unittest.TestCase):
    def test_nominal_chunk_time_and_speed(self):
        state=[0.]*5+[20.];r=e.limit_chunk([state]*50,state)
        self.assertAlmostEqual(r['nominal_execution_seconds'],50/30)
        self.assertEqual(r['max_body_degrees_per_second'],15.)
        self.assertEqual(r['clip_steps'],0)
    def test_large_targets_are_recorded_as_clipped(self):
        state=[0.]*5+[20.];action=[10.]*5+[20.]
        self.assertGreater(e.limit_chunk([action]*50,state)['clip_steps'],0)
    def test_gripper_invalid_does_not_create_safe_simulation(self):
        state=[0.]*5+[20.];action=[0.]*5+[101.]
        r=e.limit_chunk([action]*50,state)
        self.assertEqual(r['simulation_blocked_step'],0)
    def test_eval_has_no_device_connect_or_write_calls(self):
        for module in (e,p):
            tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    self.assertNotIn(node.func.attr,{'connect','send_action','sync_write','configure','calibrate','enable_torque','disable_torque'})
    def test_source_exclusions_are_zero_based_and_known(self):
        self.assertEqual(p.SOURCES['red'][1],{1})
        self.assertEqual(p.SOURCES['blue'][1],{0,3,4,9,20,34})
    def test_numeric_scalar_state_is_converted_before_simulation(self):
        state=[Decimal('0')]*5+[Decimal('20')]
        r=e.limit_chunk([[0.]*5+[20.]]*50,state)
        self.assertNotIn('simulation_blocked_step',r)

if __name__=='__main__':unittest.main()
