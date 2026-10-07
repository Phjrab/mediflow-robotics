"""Exercise optional rollout tracing with stubs; no robot or camera is opened."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image


SOURCE = Path(__file__).parent / "jetson_py310_src/lerobot/rollout/strategies/base.py"
CORE_SOURCE = Path(__file__).parent / "jetson_py310_src/lerobot/rollout/strategies/core.py"


def load_local_base():
    # Initialise the installed package before swapping source modules, avoiding
    # its __init__ importing a partially constructed replacement core.
    importlib.import_module("lerobot.rollout.strategies")
    modules = {}
    for stem in ("tracking_gate", "core", "base"):
        fullname = f"lerobot.rollout.strategies.{stem}"
        spec = importlib.util.spec_from_file_location(fullname, SOURCE.with_name(f"{stem}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        modules[stem] = module
    return modules["base"], modules["core"], modules["tracking_gate"]


class FakeRobot:
    def __init__(self, stop: Event) -> None:
        self.stop = stop
        self.reads = 0

    def get_observation(self) -> dict:
        self.reads += 1
        if self.reads == 2:
            self.stop.set()
        return {
            "shoulder_pan.pos": float(self.reads),
            "ceiling_vertical": np.full((8, 8, 3), 42, dtype=np.uint8),
            "ceiling_oblique": np.full((8, 8, 3), 84, dtype=np.uint8),
        }


class TraceTest(unittest.TestCase):
    def test_two_samples_are_buffered_and_written(self) -> None:
        module, _, _ = load_local_base()

        stop = Event()
        robot = FakeRobot(stop)
        strategy = module.BaseStrategy(SimpleNamespace())
        strategy._engine = SimpleNamespace(resume=lambda: None)
        strategy._interpolator = SimpleNamespace(get_control_interval=lambda fps: 0.001)
        strategy._process_observation_and_notify = lambda processors, obs: obs
        strategy._handle_warmup = lambda *args: False
        strategy._log_telemetry = lambda *args: None
        ctx = SimpleNamespace(
            runtime=SimpleNamespace(
                cfg=SimpleNamespace(fps=15, duration=10, use_torch_compile=False),
                shutdown_event=stop,
            ),
            hardware=SimpleNamespace(robot_wrapper=robot),
            processors=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            def fake_send_next_action(*args, sent_action_out):
                sent_action_out["shoulder_pan.pos"] = 3.0
                return {"shoulder_pan.pos": 5.0}

            with patch.dict(os.environ, {"LEROBOT_ROLLOUT_JOINT_TRACE": str(trace_path)}):
                with patch.object(module, "send_next_action", side_effect=fake_send_next_action):
                    strategy.run(ctx)
            rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
            frame_dir = trace_path.with_suffix(".frames")
            manifest = [json.loads(line) for line in (frame_dir / "manifest.jsonl").read_text().splitlines()]
            for item in manifest:
                with Image.open(frame_dir / item["file"]) as image:
                    self.assertEqual(image.size, (8, 8))
        self.assertEqual(len(rows), 2)
        self.assertEqual({item["camera"] for item in manifest}, {"ceiling_vertical", "ceiling_oblique"})
        self.assertEqual(len(manifest), 2)
        self.assertEqual([row["measured"]["shoulder_pan.pos"] for row in rows], [1.0, 2.0])
        self.assertTrue(all(row["policy_target_pre_clamp"]["shoulder_pan.pos"] == 5.0 for row in rows))
        self.assertTrue(all(row["command_post_clamp"]["shoulder_pan.pos"] == 3.0 for row in rows))
        self.assertTrue(all(row["loop_work_s"] >= 0 for row in rows))

    def test_core_captures_robot_return_without_changing_command(self) -> None:
        _, module, _ = load_local_base()

        commands = []
        robot = SimpleNamespace(send_action=lambda action: commands.append(action) or {"elbow_flex.pos": 5.0})
        ctx = SimpleNamespace(
            policy=SimpleNamespace(inference=None),
            data=SimpleNamespace(dataset_features=None, ordered_action_keys=["elbow_flex.pos"]),
            processors=SimpleNamespace(robot_action_processor=lambda pair: pair[0]),
            hardware=SimpleNamespace(robot_wrapper=robot),
        )
        interpolator = SimpleNamespace(needs_new_action=lambda: False, get=lambda: [SimpleNamespace(item=lambda: 10.0)])
        sent = {}
        result = module.send_next_action({}, {}, ctx, interpolator, sent_action_out=sent)
        self.assertEqual(result, {"elbow_flex.pos": 10.0})
        self.assertEqual(commands, [{"elbow_flex.pos": 10.0}])
        self.assertEqual(sent, {"elbow_flex.pos": 5.0})


if __name__ == "__main__":
    unittest.main()
