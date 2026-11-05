import contextlib
import io
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import benchmark_offline_precision as b


class Tests(unittest.TestCase):
    def test_default_plan_does_not_import_gpu_network_or_devices(self):
        with patch('sys.argv',['bench']),patch('builtins.__import__',wraps=__import__) as imports,contextlib.redirect_stdout(io.StringIO()) as text:
            b.main()
        self.assertIn('OFFLINE_PLAN_ONLY',text.getvalue())
        self.assertFalse(any(c.args[0].startswith(('torch','lerobot','numpy','serial','urllib')) for c in imports.call_args_list))

    def test_bad_output_path_refused_before_model_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):b.output_path(Path(tmp)/'not_reports')
            with patch.object(b,'ROOT',Path(tmp)):
                path=Path(tmp)/'reports/exists';path.mkdir(parents=True)
                with self.assertRaises(ValueError):b.output_path(path)

    def test_parity_separates_joint_units_and_rejects_invalid_action(self):
        reference=[[0.]*5+[20.] for _ in range(50)];candidate=[[1.]*5+[22.] for _ in range(50)]
        rows=b.deltas(reference,candidate);self.assertEqual(rows[0]['mean_abs_delta'],1.);self.assertEqual(rows[5]['unit'],'percent_0_100')
        self.assertEqual(rows[5]['mean_abs_delta'],2.)
        for bad in ([],[[float('nan')]*6]*50,[[0.]*5+[101.]]*50):
            with self.assertRaises(ValueError):b.validate_chunk(bad)

    def test_autocast_modes_are_explicit_without_modifying_weights(self):
        calls=[];fake=types.SimpleNamespace(float16='F16',bfloat16='BF16',autocast=lambda **kw:calls.append(kw) or contextlib.nullcontext())
        for mode in b.MODES:
            with b.precision_context(fake,mode):pass
        self.assertEqual(calls,[{'device_type':'cuda','enabled':False},{'device_type':'cuda','dtype':'F16'},{'device_type':'cuda','dtype':'BF16'}])


if __name__=='__main__':unittest.main()
