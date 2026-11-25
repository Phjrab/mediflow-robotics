"""Basic ACT admission test on the legacy loop. All joint I/O is SimulatedArm."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time


WEIGHT_SHA = "9d15fc76c78db184036b88485e7db4091d8f9e7b82563dac6cbc93a65851b304"
STATS_SHA = "b6c235108ec680c5d326ff05ae653c158de03e736c6295287a15a92bf927995c"
BUNDLE = {
    "model.safetensors": WEIGHT_SHA,
    "config.json": "abaa9564b504e95d2f5f76c23790bf502bd8af8c43d5ec7cfed602a3941c4bb7",
    "policy_preprocessor.json": "f9db873197f9fcdd7da8ac59dbfe3ce26abaf76dca9074e115aa35c80928ed55",
    "policy_postprocessor.json": "e359584c9f74e9c6ffe6f801512642d97bb15610a7879e245bda5269b7d6dcc1",
    "policy_preprocessor_step_3_normalizer_processor.safetensors": STATS_SHA,
    "policy_postprocessor_step_0_unnormalizer_processor.safetensors": STATS_SHA,
}
VIEWS = ("end_effector", "ceiling_vertical", "ceiling_oblique")


def deny_motor_port(event, values):
    if event != "open" or not values or not isinstance(values[0], (str, bytes, os.PathLike)):
        return
    path = os.fsdecode(values[0])
    if any(p.startswith(("/dev/ttyACM", "/dev/ttyUSB", "/dev/serial/"))
           for p in (path, os.path.realpath(path))):
        raise RuntimeError("ACT dry-run: 모터 포트 접근 차단")


class ActPolicy:
    control_hz = 30

    def __init__(self, checkpoint, steps):
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class, make_pre_post_processors

        if steps not in (25, 50, 100):
            raise ValueError("Unsupported ACT action prefix")
        for filename, expected in BUNDLE.items():
            digest = hashlib.sha256()
            with (checkpoint / filename).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("ACT checkpoint checksum mismatch: " + filename)
        cfg = PreTrainedConfig.from_pretrained(checkpoint)
        if cfg.type != "act" or cfg.chunk_size != 100 or cfg.n_action_steps != 100 or cfg.use_amp:
            raise ValueError("Expected basic ACT checkpoint at step 40244")
        torch.set_num_threads(1)
        cfg.device = "cuda"
        self.model = get_policy_class("act").from_pretrained(checkpoint, config=cfg, strict=True).cuda().eval()
        self.pre, self.post = make_pre_post_processors(
            cfg, str(checkpoint), preprocessor_overrides={"device_processor": {"device": "cuda"}},
            postprocessor_overrides={"device_processor": {"device": "cpu"}},
        )
        self.steps = steps
        self.torch = torch

    def __call__(self, frames, state, origin, delay):
        import numpy as np

        begin = time.monotonic()
        torch = self.torch
        if set(frames) != set(VIEWS) or len(state) != 6 or not all(map(math.isfinite, state)):
            raise ValueError("Invalid ACT observation")
        obs = {"observation.state": torch.tensor([state], dtype=torch.float32)}
        for name in VIEWS:
            rgb = frames[name]["pixels"]
            if rgb.shape != (480, 640, 3) or rgb.dtype != np.uint8:
                raise ValueError("Invalid saved-training camera contract: " + name)
            obs["observation.images." + name] = torch.from_numpy(rgb.copy()).permute(2, 0, 1).unsqueeze(0).float().div_(255)
        with torch.inference_mode():
            actions = self.post(self.model.predict_action_chunk(self.pre(obs))).detach().cpu()
        if tuple(actions.shape) != (1, 100, 6) or not torch.isfinite(actions).all():
            raise ValueError("ACT must return 100 finite six-axis physical actions")
        return actions[0, :self.steps].tolist(), time.monotonic() - begin

    def validate_chunk(self, actions):
        import direct_live

        if len(actions) != self.steps:
            raise ValueError("Unexpected ACT prefix length")
        return [direct_live.vector(row, 6) for row in actions]


class RecordedViews:
    """Present the same three RGB views as the camera interface, with a simulated joint state."""
    def __init__(self, path, out):
        import numpy as np
        from stream_live import CameraStats

        with np.load(path, allow_pickle=False) as data:
            rgb, tasks = data["rgb"], data["tasks"]
            if rgb.shape != (21, 3, 2, 480, 640, 3) or rgb.dtype != np.uint8 or tasks.shape != (21,):
                raise ValueError("Expected the exported 21-episode three-camera observations")
            self.frames = rgb[tasks == "Put medicine bottle A into the blue basket", :, -1]
        if len(self.frames) != 7:
            raise ValueError("Expected seven recorded blue observations")
        self.out = out
        self.camera_map = dict(zip(VIEWS, VIEWS))
        self.cameras_stats = CameraStats(self.camera_map)
        self.index = 0

    def open_cameras(self, check):
        check()

    def camera_frames(self):
        return {name: {"pixels": frame, "timestamp": time.perf_counter()}
                for name, frame in zip(VIEWS, self.frames[self.index], strict=True)}

    def fresh_frames(self):
        frames = self.camera_frames()
        self.index = (self.index + 1) % len(self.frames)
        for name in VIEWS:
            self.cameras_stats.frame(name)
        return frames


class AlignmentViews:
    """Read the camera-only service's atomic RGB snapshots without changing its camera lease."""
    def __init__(self, out):
        from stream_live import CameraStats

        self.path = Path.home() / ".cache/so101-vla-monitor/alignment.json"
        self.out = out
        self.camera_map = dict(zip(VIEWS, VIEWS))
        self.cameras_stats = CameraStats(self.camera_map)
        self.stamp = None
        self.last_inference_stamp = None
        self.frames = None
        self.updates = 0
        self.max_publication_age_s = 0.

    def open_cameras(self, check):
        check()

    def camera_frames(self):
        import base64
        from io import BytesIO
        import numpy as np
        from PIL import Image

        state = json.loads(self.path.read_bytes())
        if state.get("motor_access") is not False or state.get("register_writes") != 0:
            raise ValueError("Expected a camera-only alignment publisher")
        stamp = state.get("captured")
        if state.get("phase") != "ready" or not isinstance(stamp, (int, float)):
            return None
        age = time.time() - stamp
        if not 0 <= age <= 1 or (self.stamp is not None and stamp < self.stamp):
            return None
        if stamp != self.stamp:
            if set(state.get("images", {})) != set(VIEWS):
                return None
            frames = {}
            for name in VIEWS:
                jpeg = base64.b64decode(state["images"][name], validate=True)
                with Image.open(BytesIO(jpeg)) as img:
                    if img.format != "JPEG" or img.size != (640, 480):
                        raise ValueError("Unexpected alignment frame: " + name)
                    pixels = np.array(img.convert("RGB"))
                frames[name] = {"pixels": pixels, "timestamp": time.perf_counter() - age}
                self.cameras_stats.frame(name)
            self.frames, self.stamp = frames, stamp
            self.updates += 1
        self.max_publication_age_s = max(self.max_publication_age_s, age)
        return self.frames

    def fresh_frames(self):
        frames = self.camera_frames()
        if frames is None or self.stamp == self.last_inference_stamp:
            return None
        self.last_inference_stamp = self.stamp
        return frames


def run(args):
    sys.addaudithook(deny_motor_port)
    import live_start
    from config_runtime import load
    from startup_simulation import run_case

    live_start.ACTIVE_SPEED = args.speed
    out = Path(__file__).resolve().parents[1] / "runs" / f"DRY_ACT_{time.time_ns()}"
    out.mkdir(parents=True)
    steps = args.n_action_steps or 100
    source = "saved_rgb" if args.act_recorded_inputs else "camera"
    if args.act_alignment_preview:
        source = "live_alignment_jpeg"
    camera = None
    result = dict(policy="act", policy_family="ACT", language_input=False, checkpoint_step=40244,
                  expected_weight_sha256=WEIGHT_SHA, n_action_steps=steps, native_chunk=100,
                  target_control_hz=30, requested_duration_s=args.duration, requested_speed=args.speed,
                  frame_source=source, joint_source="SimulatedArm", physical_motor_writes=0, passed=False)
    try:
        policy = ActPolicy(args.act_checkpoint.expanduser().resolve(), steps)
        result["verified_weight_sha256"] = WEIGHT_SHA
        if args.act_recorded_inputs:
            camera = RecordedViews(args.act_recorded_inputs, out)
        elif args.act_alignment_preview:
            camera = AlignmentViews(out)
        else:
            from stream_live import device_class
            from config_runtime import contract

            # Only open_cameras is used by run_case. The motor client is never connected.
            camera = device_class()({"contract": contract()}, lambda row: None, out)
        result.update(run_case("act_dry", args.duration, args.speed, load()["home"]["state"],
                               {}, policy, real_time=True, camera_source=camera))
    except (KeyboardInterrupt, Exception) as exc:
        result.update(status="HOLD", passed=False, reason=f"{type(exc).__name__}: {exc}")
    finally:
        if isinstance(camera, AlignmentViews):
            result.update(distinct_preview_snapshots=camera.updates,
                          max_preview_publication_age_s=camera.max_publication_age_s)
        if camera is not None and source == "camera":
            try:
                camera.close()
                if camera.cleanup_errors:
                    raise RuntimeError(str(camera.cleanup_errors))
            except Exception as exc:
                result.update(passed=False, cleanup_error=str(exc))
        payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        (out / "summary.json").write_text(payload + "\n", encoding="utf-8")
        print(out, payload, flush=True)
    return 0 if result["passed"] else 1
