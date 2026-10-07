"""Safety-relevant sequencing tests with a delayed/stalled fake robot."""

import json
import os
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import torch
from lerobot.utils.action_interpolator import ActionInterpolator

from lerobot_training.test_rollout_joint_trace_no_robot import load_local_base


class SyncInferenceEngine:
    """Stub name matches the runtime's explicit sync-engine restriction."""
    def __init__(self, actions):
        self.actions = actions
        self.consumed = 0

    def resume(self):
        pass

    def get_action(self, observation):
        result = self.actions[min(self.consumed, len(self.actions)-1)]
        self.consumed += 1
        return torch.tensor(result, dtype=torch.float32)


class FeedbackRobot:
    def __init__(self, joints, event, stalled=False, stop_after_reads=80, steady_elbow_error=0.):
        self.joints = joints
        self.event = event
        self.position = {k: 0.0 for k in joints}
        self.commands = []
        self.reads = 0
        self.stalled = stalled
        self.stop_after_reads = stop_after_reads
        self.steady_elbow_error = steady_elbow_error

    def get_observation(self):
        self.reads += 1
        if self.reads > self.stop_after_reads or (self.commands and self.position["gripper.pos"] >= 40):
            self.event.set()
        return self.position.copy()

    def send_action(self, action):
        # Preserve the real run's 5-degree relative-target clamp, then simulate
        # a slow servo (one degree/tick). Gripper responds immediately.
        sent = {k: self.position[k] + max(-5, min(5, v-self.position[k])) for k,v in action.items()}
        self.commands.append((self.position.copy(), action.copy(), sent))
        if not self.stalled:
            for k,v in sent.items():
                equilibrium = max(0., v-self.steady_elbow_error) if k == "elbow_flex.pos" else v
                step = equilibrium-self.position[k]
                self.position[k] += max(-1, min(1, step)) if k != "gripper.pos" else step
        return sent


class GateTests(unittest.TestCase):
    def setUp(self):
        self.base, self.core, self.gate_module = load_local_base()
        self.joints = list(self.gate_module.BODY_JOINTS) + ["gripper.pos"]
        self.config = json.dumps({"schema_version": 1,
            "body_tolerance_deg": {k: 3 for k in self.gate_module.BODY_JOINTS},
            "max_target_wait_s": 2})

    def run_stub(self, stalled=False, stop_after_reads=80, steady_elbow_error=0., config=None, expect_fault=False):
        stop = Event()
        robot = FeedbackRobot(self.joints, stop, stalled, stop_after_reads, steady_elbow_error)
        # Elbow target10 while grip remains20, then a release40 only after body catches up.
        first = [0.,0.,10.,0.,0.,20.]
        release = [0.,0.,10.,0.,0.,40.]
        engine = SyncInferenceEngine([first, release])
        ctx = SimpleNamespace(
            runtime=SimpleNamespace(cfg=SimpleNamespace(fps=15, duration=30, use_torch_compile=False), shutdown_event=stop),
            hardware=SimpleNamespace(robot_wrapper=robot),
            processors=SimpleNamespace(robot_action_processor=lambda pair: pair[0]),
            policy=SimpleNamespace(inference=engine),
            data=SimpleNamespace(dataset_features=None, ordered_action_keys=self.joints))
        strategy = self.base.BaseStrategy(SimpleNamespace())
        strategy._engine = engine
        strategy._interpolator = ActionInterpolator(multiplier=2)
        strategy._process_observation_and_notify = lambda processors, obs: obs
        strategy._handle_warmup = lambda *args: False
        strategy._log_telemetry = lambda *args: None
        clock = [0.]
        def advance_clock():
            clock[0] += .05
            return clock[0]
        patches = (
            patch.dict(os.environ, {"LEROBOT_ROLLOUT_TRACKING_GATE_JSON": config or self.config}),
            patch.object(self.core, "build_dataset_frame", side_effect=lambda features,obs,prefix:obs),
            patch.object(self.base.time, "perf_counter", side_effect=advance_clock),
            patch.object(self.base, "precise_sleep", return_value=None),
            patch.object(self.base.logger, "warning"),
            patch.object(self.base.logger, "exception"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            if stalled or expect_fault:
                with self.assertRaises(self.gate_module.TrackingGateError):
                    strategy.run(ctx)
            else:
                strategy.run(ctx)
        return robot,engine,strategy,ctx

    def test_delayed_body_does_not_consume_release_while_catching_up(self):
        robot,engine,strategy,ctx = self.run_stub()
        releases = [(before, action) for before,action,sent in robot.commands if action["gripper.pos"] > 20]
        self.assertTrue(releases)
        self.assertGreaterEqual(releases[0][0]["elbow_flex.pos"], 7)
        self.assertGreater(len(robot.commands), engine.consumed)
        self.assertTrue(all(abs(sent[k]-before[k]) <= 5 for before,action,sent in robot.commands for k in self.joints))

    def test_stall_times_out_without_consuming_release_or_auto_return(self):
        robot,engine,strategy,ctx = self.run_stub(stalled=True)
        self.assertEqual(engine.consumed, 1)
        self.assertTrue(all(action["gripper.pos"] == 20 for before,action,sent in robot.commands))
        self.assertTrue(ctx.runtime.shutdown_event.is_set())
        ctx.runtime.cfg.return_to_initial_position = True
        with patch.object(strategy, "_teardown_hardware") as teardown:
            strategy.teardown(ctx)
        self.assertFalse(teardown.call_args.kwargs["return_to_initial_position"])
        self.assertTrue(teardown.call_args.kwargs["hold_position_on_disconnect"])

    def test_missing_or_nonfinite_feedback_fails_closed(self):
        gate = self.gate_module.BodyTrackingGate.from_json(self.config)
        positions = {k:0. for k in self.gate_module.BODY_JOINTS}
        gate.set_target(positions,0.)
        for bad in ({}, {**positions,"elbow_flex.pos":float("nan")}):
            with self.assertRaises(self.gate_module.TrackingGateError):
                gate.inspect(bad,.1)

    def test_stop_received_during_observation_sends_no_further_action(self):
        robot,engine,strategy,ctx = self.run_stub(stop_after_reads=1)
        self.assertEqual(len(robot.commands),1)
        self.assertEqual(engine.consumed,1)
        self.assertTrue(strategy._tracking_faulted)

    def test_gripper_contact_does_not_block_body_completion(self):
        gate = self.gate_module.BodyTrackingGate.from_json(self.config)
        positions = {k:0. for k in self.gate_module.BODY_JOINTS}
        gate.set_target({**positions,"gripper.pos":5},0.)
        self.assertFalse(gate.inspect({**positions,"gripper.pos":25},.1).hold)

    def test_measured_steady_offset_can_pass_4deg_but_large_lag_still_holds(self):
        robot,engine,_,_ = self.run_stub(steady_elbow_error=3.45,expect_fault=True)
        self.assertEqual(engine.consumed,1)
        config=json.loads(self.config)
        config["body_tolerance_deg"]["elbow_flex.pos"]=4.
        robot,engine,_,_ = self.run_stub(steady_elbow_error=3.45,config=json.dumps(config))
        self.assertTrue(any(action["gripper.pos"] > 20 for before,action,sent in robot.commands))
        gate=self.gate_module.BodyTrackingGate.from_json(json.dumps(config))
        target={key:0. for key in self.gate_module.BODY_JOINTS}
        gate.set_target(target,0.)
        self.assertTrue(gate.inspect({**target,"elbow_flex.pos":21.},.1).hold)

    def test_nonfinite_target_is_rejected_before_any_motor_command(self):
        commands=[]
        ctx=SimpleNamespace(processors=SimpleNamespace(robot_action_processor=lambda pair:pair[0]),
                            hardware=SimpleNamespace(robot_wrapper=SimpleNamespace(send_action=lambda action:commands.append(action))))
        target={k:0. for k in self.joints}
        target["gripper.pos"]=float("nan")
        with self.assertRaises(self.gate_module.TrackingGateError):
            self.core.send_action_dict(target,{},ctx,target_action_out={})
        self.assertEqual(commands,[])

    def test_one_encoder_tick_margin_accepts_boundary_but_not_material_lag(self):
        config=json.loads(self.config)
        config["body_tolerance_deg"]["elbow_flex.pos"]=4.
        config["measurement_margin_deg"]=.1
        gate=self.gate_module.BodyTrackingGate.from_json(json.dumps(config))
        target={key:0. for key in self.gate_module.BODY_JOINTS}
        gate.set_target(target,0.)
        self.assertFalse(gate.inspect({**target,"elbow_flex.pos":4.014898},.1).hold)
        self.assertTrue(gate.inspect({**target,"elbow_flex.pos":4.2},.1).hold)
        self.assertTrue(gate.inspect({**target,"elbow_flex.pos":21.},.1).hold)

    def check_cleanup(self, hold, read_error=False):
        from unittest.mock import Mock
        commands,disconnect_torque_flags=[],[]
        positions={"elbow_flex":12.5,"gripper":25.}
        config=SimpleNamespace(disable_torque_on_disconnect=True)
        bus=SimpleNamespace(motors={key:object() for key in positions},
                            sync_read=Mock(side_effect=OSError("simulated read loss")) if read_error else Mock(return_value=positions))
        def disconnect():
            disconnect_torque_flags.append(config.disable_torque_on_disconnect)
        inner=SimpleNamespace(is_connected=True,config=config,bus=bus,disconnect=disconnect)
        wrapper=SimpleNamespace(inner=inner,send_action=lambda action:commands.append(action))
        hw=SimpleNamespace(robot_wrapper=wrapper,initial_position=None,teleop=None)
        strategy=self.base.BaseStrategy(SimpleNamespace())
        with patch.object(self.core.logger,"warning"),patch.object(self.core.logger,"exception"):
            strategy._teardown_hardware(hw,return_to_initial_position=False,hold_position_on_disconnect=hold)
        self.assertTrue(config.disable_torque_on_disconnect)
        return commands,disconnect_torque_flags,bus

    def test_tracking_stop_freezes_present_pose_and_preserves_torque(self):
        commands,flags,bus=self.check_cleanup(True)
        self.assertEqual(commands,[{"elbow_flex.pos":12.5,"gripper.pos":25.}])
        self.assertEqual(flags,[False])
        bus.sync_read.assert_called_once_with("Present_Position",num_retry=2)

    def test_read_loss_preserves_torque_without_sending_guessed_positions(self):
        commands,flags,bus=self.check_cleanup(True,read_error=True)
        self.assertEqual(commands,[])
        self.assertEqual(flags,[False])

    def test_standard_nontracking_cleanup_is_unchanged(self):
        commands,flags,bus=self.check_cleanup(False)
        self.assertEqual(commands,[])
        self.assertEqual(flags,[True])
        bus.sync_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
