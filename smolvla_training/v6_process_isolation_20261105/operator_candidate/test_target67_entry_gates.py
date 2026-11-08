import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import arm_source
from first_trial_core import JOINTS,Hold
from operator_arm import ArmingAdapter
from trial_runtime import DeviceIO


class Handler:
    def txRxPacket(self,*args):raise AssertionError("합성 검사에서 통신 금지")


class Tests(unittest.TestCase):
    def inspection(self, feedback):
        io=DeviceIO({"home":dict(joint_names=list(JOINTS),state=[0.]*6,tolerance_deg=5.)},expected_torque=0)
        bus=SimpleNamespace(packet_handler=Handler(),motors={j:SimpleNamespace(id=i) for i,j in enumerate(JOINTS,1)},
                            _normalize=lambda values:{key:value-1000 for key,value in values.items()})
        io.robot=SimpleNamespace(bus=bus)
        io.snapshot=lambda:dict(state=[0.]*6)
        io.raw=lambda name:dict.fromkeys(JOINTS,1000 if name=="Present_Position" else 0)
        io.joint_bounds=lambda:[(-100.,100.)]*5+[(0.,100.)]
        io.permission=SimpleNamespace(goal_packets=0)
        with patch("individual_reply_guard.read_target_feedback",return_value=dict.fromkeys(JOINTS,feedback)):
            return io.inspect_goal()

    def test_mock_inspection_uses_target67_even_if_control42_zero(self):
        result=self.inspection(1000)
        self.assertTrue(result["arm_ready"])
        self.assertFalse(result["goal_exact_match"])
        self.assertTrue(result["active_target_exact_match"])

    def test_mock_inspection_bad_active_target_refuses(self):
        self.assertFalse(self.inspection(1001)["arm_ready"])

    def test_legacy_enable_stop_requires_new_operator_review(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/"FIELD_STOP.json").write_text("{}")
            with patch.object(arm_source,"__file__",str(root/"arm_source.py")), \
                 patch("sys.argv",["arm_source.py","--enable","--execute"]), \
                 patch("builtins.input",side_effect=AssertionError("장치 전 사용자 입력 금지")), \
                 self.assertRaisesRegex(RuntimeError,"현장 중단 검토"):
                arm_source.main()

    def test_review_flag_cannot_be_used_for_disable(self):
        with patch("sys.argv",["arm_source.py","--disable","--execute","--review-stop-for-hold-only"]), \
             self.assertRaisesRegex(RuntimeError,"전용"):
            arm_source.main()

    def test_review_never_accepts_missing_target_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/"FIELD_STOP.json").write_text("{}")
            with patch.object(arm_source,"__file__",str(root/"arm_source.py")), \
                 patch("sys.argv",["arm_source.py","--enable","--execute","--review-stop-for-hold-only"]), \
                 patch("review_saved_readiness.review",return_value={"current_target_readback_verified":False}), \
                 patch("builtins.input",side_effect=AssertionError("불충분한 근거에서 입력 금지")), \
                 self.assertRaisesRegex(RuntimeError,"근거 불충족"):
                arm_source.main()

    def test_policy_arm_remains_blocked_by_original_stop(self):
        import operator_arm
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/"FIELD_STOP.json").write_text("{}")
            with patch.object(operator_arm,"__file__",str(root/"operator_arm.py")),self.assertRaises(Hold):
                ArmingAdapter({},lambda _:None).connect()


if __name__=="__main__":unittest.main()
