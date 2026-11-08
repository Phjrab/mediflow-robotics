from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import hold_continuation as held
import trial_runtime
from first_trial_core import Hold
from test_hold_continuation import fixture


class Tests(unittest.TestCase):
    def test_plain_fake_approval_is_refused_before_any_dependency_or_device(self):
        fake=SimpleNamespace(root=Path(trial_runtime.__file__).resolve().parent,require_claimed=lambda *args:None)
        with patch.object(trial_runtime,"dependencies",side_effect=AssertionError("장치 단계 진입 금지")),self.assertRaises(Hold):
            trial_runtime.DeviceIO({},expected_torque=1,continuation=fake).connect()

    def test_unclaimed_real_receipt_is_refused_before_hardware(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            with patch.multiple(held,**pins),patch.object(trial_runtime,"__file__",str(root/"trial_runtime.py")):
                receipt=held.HoldContinuation(root,log,home=home)
                with patch.object(trial_runtime,"dependencies",side_effect=AssertionError("장치 단계 진입 금지")),self.assertRaises(Hold):
                    trial_runtime.DeviceIO(context,expected_torque=1,continuation=receipt).connect()

    def test_new_stop_blocks_all_io_before_hardware(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/held.NEW_STOP).write_text("{}")
            with patch.object(trial_runtime,"__file__",str(root/"trial_runtime.py")),patch.object(trial_runtime,"dependencies",side_effect=AssertionError("장치 단계 진입 금지")),self.assertRaises(Hold):
                trial_runtime.DeviceIO({},expected_torque=1).connect()

    def test_held_receipt_cannot_be_used_as_torque_off_path(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);root,log,context,pins,_=fixture(home)
            with patch.multiple(held,**pins),patch.object(trial_runtime,"__file__",str(root/"trial_runtime.py")):
                receipt=held.HoldContinuation(root,log,home=home)
                with patch.object(trial_runtime,"dependencies",side_effect=AssertionError("장치 단계 진입 금지")),self.assertRaises(Hold):
                    trial_runtime.DeviceIO(context,expected_torque=0,continuation=receipt).connect()


if __name__=="__main__":unittest.main()
