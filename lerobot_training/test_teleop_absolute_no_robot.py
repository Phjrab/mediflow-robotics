"""Absolute teleop startup/worker regression tests; no hardware imports or I/O."""

import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parent / "jetson_py310_src/lelab"


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


alignment_module = load_source("teleop_alignment_test_source", SOURCE / "teleop_alignment.py")
JOINTS = alignment_module.JOINTS


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.start = {name: 30. for name in JOINTS}
        self.leader = {f"{name}.pos": 0. for name in JOINTS}
        self.align = alignment_module.AbsoluteTeleopAlignment(self.start, 0.)

    def test_first_command_is_current_follower_pose(self):
        action, ready = self.align.command(self.leader, self.start, 0.)
        self.assertEqual(action, {f"{k}.pos": v for k,v in self.start.items()})
        self.assertFalse(ready)

    def test_midpoint_blends_instead_of_retaining_offset(self):
        action, ready = self.align.command(self.leader, self.start, 1.5)
        self.assertEqual(action["elbow_flex.pos"], 15.)
        self.assertFalse(ready)

    def test_after_alignment_follows_absolute_not_delta(self):
        measured = {name: 0. for name in JOINTS}
        self.assertTrue(self.align.command(self.leader, measured, 3.)[1])
        moved = {**self.leader, "elbow_flex.pos": 10.}
        self.assertEqual(self.align.command(moved, None, 4.)[0]["elbow_flex.pos"], 10.)

    def test_elapsed_time_alone_does_not_enable_direct_following(self):
        action,ready = self.align.command(self.leader, self.start, 4.)
        self.assertFalse(ready)
        with self.assertRaises(RuntimeError):
            self.align.command(self.leader, self.start, 15.)

    def test_small_residual_passes_but_large_error_does_not(self):
        measured = {name: 0. for name in JOINTS}
        self.assertFalse(self.align.command(self.leader, {**measured,"elbow_flex":5.}, 3.)[1])
        self.assertTrue(self.align.command(self.leader, {**measured,"elbow_flex":3.45}, 3.1)[1])

    def test_missing_or_nonfinite_feedback_rejected(self):
        for bad in ({}, {**self.start,"elbow_flex":math.nan}):
            with self.assertRaises((KeyError,ValueError)):
                self.align.command(self.leader, bad, 1.)

    def test_nonfinite_leader_rejected_even_after_alignment(self):
        self.align.ready = True
        with self.assertRaises(ValueError):
            self.align.command({**self.leader,"gripper.pos":math.inf}, None, 4.)

    def test_gripper_stays_in_normalized_range(self):
        self.align.ready=True
        self.assertEqual(self.align.command({**self.leader,"gripper.pos":200.},None,4.)[0]["gripper.pos"],100.)


class WorkerTests(unittest.TestCase):
    def run_worker(self, stalled=False, stop_after=80, inference_active=False):
        devices,commands,disconnect_flags = [],[],[]
        namespace={}
        class FakeModel:
            def __init__(self,**values):
                self.__dict__.update(values)
        def config(**values):
            return SimpleNamespace(**values,disable_torque_on_disconnect=True)
        class Bus:
            def __init__(self,positions):
                self.positions=positions
                self.is_connected=False
            def connect(self): self.is_connected=True
            def sync_read(self,*args,**kwargs): return self.positions.copy()
            def write_calibration(self,*args): pass
        class Follower:
            def __init__(self,cfg):
                self.config=cfg
                self.bus=Bus({name:30. for name in JOINTS})
                self.calibration={}
                self.cameras={}
                devices.append(self)
            def configure(self): pass
            def send_action(self,action):
                commands.append((action.copy(),self.config.max_relative_target.copy()))
                if not stalled:
                    for feature,target in action.items():
                        name=feature.removesuffix(".pos")
                        position=self.bus.positions[name]
                        cap=self.config.max_relative_target[name]
                        self.bus.positions[name]=position+max(-cap,min(cap,target-position))
                if len(commands)>=stop_after:
                    namespace["module"].teleoperation_active=False
            def disconnect(self):
                disconnect_flags.append(self.config.disable_torque_on_disconnect)
                self.bus.is_connected=False
        class Leader:
            def __init__(self,cfg):
                self.bus=Bus({name:0. for name in JOINTS})
                self.calibration={}
            def configure(self): pass
            def get_action(self): return {f"{name}.pos":0. for name in JOINTS}
            def disconnect(self): self.bus.is_connected=False
        class InlineThread:
            def __init__(self,target,**kwargs): self.target=target
            def start(self): self.target()
        modules={}
        def stub(name,**attributes):
            result=ModuleType(name)
            result.__dict__.update(attributes)
            modules[name]=result
            return result
        package=stub("restore_teleop_test",__path__=[])
        stub("restore_teleop_test.utils",__path__=[])
        stub("restore_teleop_test.utils.config",setup_calibration_files=lambda *args:("leader","follower"))
        stub("restore_teleop_test.utils.devices",safe_disconnect_device=lambda device,logger:device.disconnect() if device else None)
        stub("restore_teleop_test.record",recording_active=False)
        stub("restore_teleop_test.rollout",inference_active=inference_active)
        stub("pydantic",BaseModel=FakeModel)
        stub("lerobot.robots.so_follower",SO101Follower=Follower,SO101FollowerConfig=config)
        stub("lerobot.teleoperators.so_leader",SO101Leader=Leader,SO101LeaderConfig=config)
        clock=[0.]
        def tick():
            clock[0]+=.05
            return clock[0]
        with patch.dict(sys.modules,modules):
            load_source("restore_teleop_test.teleop_alignment",SOURCE/"teleop_alignment.py")
            module=load_source("restore_teleop_test.teleoperate",SOURCE/"teleoperate.py")
            namespace["module"]=module
            with patch.object(module.threading,"Thread",InlineThread),patch.object(module.time,"monotonic",side_effect=tick),patch.object(module.time,"sleep"),patch.object(module.logger,"exception"),patch.object(module.logger,"error"),patch.object(module.logger,"warning"):
                result=module.handle_start_teleoperation(FakeModel(leader_port="fake",follower_port="fake",leader_config="fake",follower_config="fake"))
        return result,commands,disconnect_flags,devices,module

    def test_worker_uses_startup_caps_then_absolute_direct_targets(self):
        result,commands,flags,devices,module=self.run_worker()
        self.assertTrue(result["success"])
        self.assertAlmostEqual(commands[0][0]["elbow_flex.pos"],30.,delta=.1)
        self.assertEqual(commands[0][1]["elbow_flex"],5.)
        self.assertEqual(commands[0][1]["gripper"],2.)
        direct=[action for action,cap in commands if cap["elbow_flex"]==360.]
        self.assertTrue(direct)
        self.assertTrue(all(action["elbow_flex.pos"]==0. for action in direct))
        self.assertEqual(flags,[True])  # normal teleop cleanup is unchanged
        self.assertFalse(module.teleoperation_active)

    def test_alignment_timeout_never_enables_direct_and_retains_torque(self):
        result,commands,flags,devices,module=self.run_worker(stalled=True,stop_after=1000)
        self.assertTrue(commands)
        self.assertTrue(all(cap["elbow_flex"]==5. for action,cap in commands))
        self.assertEqual(commands[-1][0]["elbow_flex.pos"],30.)
        self.assertEqual(flags,[False])
        self.assertFalse(module.teleoperation_active)

    def test_inference_conflict_opens_no_devices(self):
        result,commands,flags,devices,module=self.run_worker(inference_active=True)
        self.assertFalse(result["success"])
        self.assertEqual(devices,[])
        self.assertEqual(commands,[])


if __name__ == "__main__":
    unittest.main()
