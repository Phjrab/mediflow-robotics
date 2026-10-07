# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Base rollout strategy: autonomous policy execution with no data recording."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image

from lerobot.utils.robot_utils import precise_sleep

from ..context import RolloutContext
from .core import RolloutStrategy, send_action_dict, send_next_action
from .tracking_gate import BodyTrackingGate, TrackingGateError

logger = logging.getLogger(__name__)


class BaseStrategy(RolloutStrategy):
    """Autonomous policy rollout with optional, buffered joint/timing diagnostics.

    All actions still flow through the original robot-action processor.
    The trace records the policy target before the robot's relative-target
    clamp and the actual command returned by the robot after the clamp.
    """

    def setup(self, ctx: RolloutContext) -> None:
        """Initialise the inference engine."""
        self._init_engine(ctx)
        logger.info("Base strategy ready")

    def run(self, ctx: RolloutContext) -> None:
        """Run the autonomous control loop until shutdown or duration expires."""
        engine = self._engine
        cfg = ctx.runtime.cfg
        robot = ctx.hardware.robot_wrapper
        interpolator = self._interpolator
        control_interval = interpolator.get_control_interval(cfg.fps)

        trace_path = os.environ.get("LEROBOT_ROLLOUT_JOINT_TRACE")
        gate_json = os.environ.get("LEROBOT_ROLLOUT_TRACKING_GATE_JSON")
        self._tracking_gate_enabled = bool(gate_json)
        self._tracking_faulted = bool(gate_json)
        gate = BodyTrackingGate.from_json(gate_json) if gate_json else None
        if gate and type(engine).__name__ != "SyncInferenceEngine":
            raise ValueError("Body tracking gate supports only synchronous absolute-action inference")
        self._tracking_faulted = False
        if gate:
            logger.info("Body-tracking progression enabled: tolerances_deg=%s, max_wait_s=%s", gate.tolerances, gate.max_wait)
        previous_action: dict | None = None
        obs: dict | None = None
        tracking_held = False
        trace_rows: list[dict] | None = [] if trace_path else None
        # Copy frames already present in the observation. Never open another camera
        # or perform JPEG encoding in the time-sensitive control loop.
        frame_samples: list[tuple[float, str, np.ndarray]] | None = [] if trace_path else None
        next_frame_sample_s = 0.0
        start_time = time.perf_counter()
        engine.resume()
        logger.info("Base strategy control loop started")

        try:
            while not ctx.runtime.shutdown_event.is_set():
                loop_start = time.perf_counter()

                if cfg.duration > 0 and (loop_start - start_time) >= cfg.duration:
                    logger.info("Duration limit reached (%.0fs)", cfg.duration)
                    if tracking_held:
                        self._tracking_faulted = True
                    break

                obs = robot.get_observation()
                obs_done = time.perf_counter()
                elapsed_s = loop_start - start_time
                if frame_samples is not None and elapsed_s >= next_frame_sample_s:
                    for camera_name in ("ceiling_vertical", "ceiling_oblique"):
                        # Robot observation keys are usually bare camera names;
                        # policy feature names may carry observation.images.
                        image = obs.get(camera_name)
                        if image is None:
                            image = obs.get(f"observation.images.{camera_name}")
                        if isinstance(image, np.ndarray) and image.ndim == 3 and image.shape[-1] == 3:
                            frame_samples.append((elapsed_s, camera_name, image.copy()))
                    next_frame_sample_s = elapsed_s + 2.0
                obs_processed = self._process_observation_and_notify(ctx.processors, obs)
                process_done = time.perf_counter()

                if self._handle_warmup(cfg.use_torch_compile, loop_start, control_interval):
                    continue
                if gate and ctx.runtime.shutdown_event.is_set():
                    break

                decision = gate.inspect(obs, loop_start) if gate else None
                tracking_held = bool(decision and decision.hold)
                sent_action = {} if trace_rows is not None else None
                robot_target = {} if gate else None
                if tracking_held:
                    # Keep sending the previous complete action with fresh feedback,
                    # without consuming another policy/interpolation sample.
                    action_dict = previous_action
                    send_action_dict(
                        action_dict, obs, ctx, sent_action_out=sent_action,
                        target_action_out=robot_target,
                    )
                else:
                    send_args = {"sent_action_out": sent_action}
                    if gate:
                        send_args["target_action_out"] = robot_target
                    action_dict = send_next_action(obs_processed, obs, ctx, interpolator, **send_args)
                    if gate and action_dict is not None:
                        gate.set_target(robot_target, time.perf_counter())
                        previous_action = action_dict.copy()
                action_done = time.perf_counter()
                self._log_telemetry(obs_processed, action_dict, ctx.runtime)

                dt = time.perf_counter() - loop_start
                if trace_rows is not None:
                    trace_rows.append({
                        "elapsed_s": round(loop_start - start_time, 6),
                        "observation_s": round(obs_done - loop_start, 6),
                        "process_s": round(process_done - obs_done, 6),
                        "infer_and_send_s": round(action_done - process_done, 6),
                        "loop_work_s": round(dt, 6),
                        "measured": {k: float(v) for k, v in obs.items() if k.endswith(".pos")},
                        "policy_target_pre_clamp": (
                            {k: float(v) for k, v in action_dict.items()} if action_dict is not None else None
                        ),
                        "command_post_clamp": (
                            {k: float(v) for k, v in sent_action.items()} if sent_action else None
                        ),
                        "tracking_gate": ({
                            "held": tracking_held,
                            "previous_body_errors_deg": decision.errors_deg,
                            "target_age_s": decision.target_age_s,
                        } if decision else None),
                    })
                if (sleep_t := control_interval - dt) > 0:
                    precise_sleep(sleep_t)
                else:
                    logger.warning(
                        f"Record loop is running slower ({1 / dt:.1f} Hz) than the target FPS ({cfg.fps} Hz). Dataset frames might be dropped and robot control might be unstable. Common causes are: 1) Camera FPS not keeping up 2) Policy inference taking too long 3) CPU starvation"
                    )
        except TrackingGateError:
            self._tracking_faulted = True
            ctx.runtime.shutdown_event.set()
            logger.exception("Stopping rollout because body tracking could not continue")
            raise
        finally:
            if gate and tracking_held:
                self._tracking_faulted = True
            if gate and ctx.runtime.shutdown_event.is_set():
                self._tracking_faulted = True
            if gate and obs is not None and gate.target is not None:
                try:
                    if gate.inspect(obs, time.perf_counter()).hold:
                        self._tracking_faulted = True
                except TrackingGateError:
                    self._tracking_faulted = True
            if trace_rows is not None and trace_path:
                try:
                    path = Path(trace_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("w") as handle:
                        for row in trace_rows:
                            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                    logger.info("Buffered rollout joint trace saved: %s (%d samples)", path, len(trace_rows))
                except OSError as exc:
                    logger.warning("Could not save rollout joint trace: %s", exc)
            if frame_samples and trace_path:
                try:
                    frame_dir = Path(trace_path).with_suffix(".frames")
                    frame_dir.mkdir(parents=True, exist_ok=True)
                    manifest_path = frame_dir / "manifest.jsonl"
                    with manifest_path.open("w") as manifest:
                        for index, (elapsed_s, camera_name, image) in enumerate(frame_samples):
                            filename = f"{index:04d}_{camera_name}_{elapsed_s:06.2f}s.jpg"
                            Image.fromarray(image).save(frame_dir / filename, format="JPEG", quality=75)
                            manifest.write(json.dumps({
                                "elapsed_s": round(elapsed_s, 6),
                                "camera": camera_name,
                                "file": filename,
                            }, separators=(",", ":")) + "\n")
                    logger.info("Rollout camera frame trace saved: %s (%d frames)", frame_dir, len(frame_samples))
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning("Could not save rollout camera frame trace: %s", exc)

    def teardown(self, ctx: RolloutContext) -> None:
        """Disconnect hardware and stop inference."""
        self._teardown_hardware(
            ctx.hardware,
            return_to_initial_position=(
                ctx.runtime.cfg.return_to_initial_position
                and not getattr(self, "_tracking_faulted", False)
            ),
            hold_position_on_disconnect=(
                getattr(self, "_tracking_gate_enabled", False)
                and getattr(self, "_tracking_faulted", False)
            ),
        )
        logger.info("Base strategy teardown complete")
