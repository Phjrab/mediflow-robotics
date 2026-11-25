"""Benchmark trained policies and the legacy scheduler using saved frames only."""
import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time

import numpy as np

VIEWS = tuple("observation.images." + name for name in
              ("end_effector", "ceiling_vertical", "ceiling_oblique"))


def export(root, output):
    import av
    from pyarrow.parquet import ParquetFile

    def image(path, target):
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            container.seek(round(target / stream.time_base), stream=stream)
            for frame in container.decode(stream):
                if float(frame.pts * stream.time_base) >= target - 0.5/30:
                    if abs(float(frame.pts * stream.time_base) - target) > .5/30:
                        raise ValueError("Could not locate saved RGB frame")
                    return frame.to_ndarray(format="rgb24")
        raise ValueError("Saved RGB frame missing")

    episodes = ParquetFile(root/"meta/episodes/chunk-000/file-000.parquet").read().to_pylist()
    data = ParquetFile(root/"data/chunk-000/file-000.parquet").read(
        columns=["observation.state", "action"]).to_pylist()
    from metrics import closing

    rows, states, tasks = [], [], []
    for ep in episodes:
        start, end = ep["dataset_from_index"], ep["dataset_to_index"]
        event = closing(np.asarray([r["action"] for r in data[start:end]]))
        at = max(1, event if event is not None else (end-start)//2)
        views = []
        for view in VIEWS:
            prefix = "videos/" + view
            chunk, file = ep[prefix+"/chunk_index"], ep[prefix+"/file_index"]
            path = root/prefix/f"chunk-{chunk:03d}/file-{file:03d}.mp4"
            base = ep[prefix+"/from_timestamp"]
            views.append([image(path, base + (at-lag)/30) for lag in (1, 0)])
        rows.append(views)
        states.append([data[start + at-lag]["observation.state"] for lag in (1, 0)])
        tasks.append(ep["tasks"][0])
    np.savez_compressed(output, rgb=np.asarray(rows), state=np.asarray(states, dtype=np.float32),
                        tasks=np.array(tasks))


def measure(args):
    import resource
    import torch
    from lerobot.policies.factory import get_policy_class, make_pre_post_processors

    torch.set_num_threads(1)
    torch.manual_seed(101)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    cfg, compatibility = load_policy_config(args.checkpoint)
    cfg.device = "cuda"
    train_config = args.checkpoint/"train_config.json"
    rename = json.loads(train_config.read_text()).get("rename_map", {}) if train_config.is_file() else {}
    if args.model == "historical_smolvla":
        rename = dict(zip(VIEWS, (f"observation.images.camera{i}" for i in (1, 2, 3))))
    strict = cfg.type == "xvla" or args.act_prefixes
    if cfg.type == "smolvla" and cfg.load_vlm_weights:
        # This controls initialization before loading the full local policy.
        # Require all checkpoint tensors when skipping a redundant Hub load.
        cfg.load_vlm_weights = False
        strict = True
        compatibility.append("loaded the full local policy strictly, without redundant VLM initialization")
    policy = get_policy_class(cfg.type).from_pretrained(args.checkpoint, config=cfg, strict=strict).cuda().eval()
    pre_overrides = {"device_processor": {"device": "cuda"}}
    tokenizer = args.checkpoint/"tokenizer"
    if tokenizer.is_dir():
        pre_overrides["tokenizer_processor"] = {"tokenizer_name": str(tokenizer.resolve())}
        compatibility.append("resolved the checkpoint's bundled tokenizer directory")
    pre, post = make_pre_post_processors(cfg, str(args.checkpoint), preprocessor_overrides=pre_overrides,
                                        postprocessor_overrides={"device_processor": {"device": "cpu"}})
    decoder_contract = verify_xvla_decoder(args.checkpoint, cfg, policy, post, torch) if cfg.type == "xvla" else None
    saved = np.load(args.inputs, allow_pickle=False)
    rgb, state, tasks = saved["rgb"], saved["state"], saved["tasks"]
    observed = len(state)
    if cfg.n_obs_steps not in (1, 2):
        raise ValueError(f"Unsupported observation horizon {cfg.n_obs_steps}")
    if args.model == "act_task":
        codes = {color: list(np.eye(3, dtype=np.float32)[i]) for i, color in enumerate(("red", "green", "blue"))}
        code = np.asarray([codes[str(task).split()[-2]] for task in tasks], dtype=np.float32)
        state = np.concatenate((state, np.repeat(code[:, None, :], state.shape[1], axis=1)), axis=-1)
    temporal = None
    if cfg.type == "act":
        from lerobot.policies.act.modeling_act import ACTTemporalEnsembler
        temporal = ACTTemporalEnsembler(0.01, cfg.chunk_size)

    def infer(i, mode):
        start = time.perf_counter()
        obs = {"task": [str(tasks[i])]}
        for key, frames in zip(VIEWS, rgb[i]):
            images = torch.from_numpy(frames).permute(0, 3, 1, 2).float().div_(255)
            observation = images[-1] if cfg.n_obs_steps == 1 else images
            obs[rename.get(key, key)] = observation.unsqueeze(0)
        observation = torch.from_numpy(state[i])
        obs["observation.state"] = (observation[-1] if cfg.n_obs_steps == 1 else observation).unsqueeze(0)
        ctx = torch.autocast("cuda") if cfg.use_amp else contextlib.nullcontext()
        with torch.inference_mode(), ctx:
            batch = pre(obs)
            # Diffusion's offline method consumes batched history directly. X-VLA
            # and SmolVLA use internal queues, cleared here for saved observations.
            policy.reset()
            action = policy.predict_action_chunk(batch)
            if mode == "temporal":
                action = temporal.update(action).unsqueeze(1)
            output = post(action.float()).detach().cpu().float()
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        if output.ndim != 3 or output.shape[0] != 1 or output.shape[2] != 6 or not torch.isfinite(output).all():
            raise ValueError("Invalid six-axis output")
        return output[0].tolist(), elapsed

    with (args.checkpoint/"model.safetensors").open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else legacy_hash(stream)
    blue = [i for i in range(observed) if "blue basket" in str(tasks[i])]
    if args.duration > 0 and not blue:
        raise ValueError("No blue holdout observations")
    if args.act_prefixes:
        if args.model != "act" or cfg.type != "act" or cfg.chunk_size != 100 or state.shape[-1] != 6:
            raise ValueError("Prefix comparison requires the basic ACT policy with 100 actions and six state axes")
        run_act_prefixes(args, infer, blue, state, sha)
        return
    if args.temporal_legacy_only:
        if temporal is None:
            raise ValueError("Native temporal mode requires ACT")
        for i in range(args.warmup):
            infer(blue[i % len(blue)], "temporal")
        temporal.reset()
        run = legacy_scheduler(args.live, infer, blue, state, args.duration, 1, mode="temporal")
        row = dict(model=args.model, checkpoint_step=args.step, weights_sha256=sha, mode="temporal_legacy",
                   target_control_hz=30, speed=0.5, duration_s=args.duration, **run)
        filename(args, "scheduler").write_text(json.dumps([row], indent=2) + "\n")
        return
    modes = ["chunk"] + (["temporal"] if temporal else [])
    results = []
    for mode in modes:
        if temporal:
            temporal.reset()
        times = []
        for i in range(args.warmup + args.samples):
            actions, elapsed = infer(i % observed, mode)
            if i >= args.warmup:
                times.append(elapsed*1000)
        results.append(dict(mode=mode, mean_ms=statistics.mean(times), p95_ms=float(np.percentile(times, 95)),
                            samples=len(times), chunk_actions=len(actions), raw_ms=times,
                            temporal_ensemble_coefficient=0.01 if mode == "temporal" else None))
    info = dict(model=args.model, checkpoint_step=args.step, weights_sha256=sha, status="MEASURED",
                config_sha256=hashlib.sha256((args.checkpoint/"config.json").read_bytes()).hexdigest(),
                strict_weight_load=strict, decoder_contract=decoder_contract,
                modes=results, n_observations=observed, gpu=torch.cuda.get_device_name(), torch=str(torch.__version__),
                warmup=args.warmup, peak_allocated_mib=torch.cuda.max_memory_allocated()/2**20,
                peak_reserved_mib=torch.cuda.max_memory_reserved()/2**20,
                peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                config_compatibility=compatibility,
                timing_scope="saved RGB arrays through CPU preprocessing, GPU inference, and decoded CPU actions")
    filename(args, "latency").write_text(json.dumps(info, indent=2) + "\n")
    if args.duration <= 0:
        return
    scheduler = []
    for mode in modes:
        if temporal:
            temporal.reset()
        if mode == "temporal" and args.temporal_adapter:
            run = temporal_scheduler(infer, blue, args.duration)
            mode = "temporal_adapter"
        else:
            horizon = 1 if mode == "temporal" else results[0]["chunk_actions"]
            run = legacy_scheduler(args.live, infer, blue, state, args.duration, horizon, mode)
            if mode == "temporal":
                mode = "temporal_legacy"
        scheduler.append(dict(model=args.model, checkpoint_step=args.step, weights_sha256=sha, mode=mode,
                              target_control_hz=30, speed=0.5, duration_s=args.duration, **run))
        filename(args, "scheduler").write_text(json.dumps(scheduler, indent=2) + "\n")


def run_act_prefixes(args, infer, blue, state, weights_sha):
    provenance = dict(
        model=args.model, checkpoint_step=args.step, weights_sha256=weights_sha,
        config_sha256=hashlib.sha256((args.checkpoint/"config.json").read_bytes()).hexdigest(),
        inputs_sha256=hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
        scheduler_sha256=hashlib.sha256((args.live/"stream_live.py").read_bytes()).hexdigest(),
        benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        strict_weight_load=True, duration_s=args.duration, speed=.5, target_control_hz=30,
        native_chunk_actions=100, prefix_applied_after_full_inference_and_decode=True,
    )
    rows = []
    for horizon in (25, 50, 100):
        def prefix(index, mode):
            actions, elapsed = infer(index, mode)
            if len(actions) != 100:
                raise ValueError("Expected exactly 100 native actions")
            return actions[:horizon], elapsed

        for index in range(args.warmup):
            prefix(blue[index % len(blue)], "chunk")
        print(f"ACT prefix {horizon}/100", flush=True)
        run = legacy_scheduler(args.live, prefix, blue, state, args.duration, horizon)
        rows.append(dict(provenance, prefix_actions=horizon, **run))
        filename(args, "prefixes").write_text(json.dumps(rows, indent=2) + "\n")


def load_policy_config(checkpoint):
    from lerobot.configs.policies import PreTrainedConfig

    raw = json.loads((checkpoint/"config.json").read_text())
    if raw["type"] == "diffusion" and "gradient_checkpointing" in raw:
        from dataclasses import fields
        from lerobot.policies.diffusion.configuration_diffusion import DiffusionConfig

        if "gradient_checkpointing" not in {field.name for field in fields(DiffusionConfig)}:
            # LeRobot 0.6.0 predates this training-only setting. Keep the
            # checkpoint untouched and load the otherwise identical config.
            del raw["gradient_checkpointing"]
            with tempfile.TemporaryDirectory(prefix="diffusion-inference-config-") as temporary:
                (Path(temporary)/"config.json").write_text(json.dumps(raw))
                return PreTrainedConfig.from_pretrained(temporary), ["omitted training-only gradient_checkpointing"]
    return PreTrainedConfig.from_pretrained(checkpoint), []


def verify_xvla_decoder(checkpoint, cfg, policy, post, torch):
    from safetensors.numpy import load_file

    if (cfg.action_mode != "auto" or tuple(cfg.action_feature.shape) != (6,)
            or policy.model.action_space.real_dim != 6):
        raise ValueError("X-VLA checkpoint does not expose six ordered SO-101 actions")
    path = checkpoint/"policy_postprocessor.json"
    steps = json.loads(path.read_text())["steps"]
    if [s["registry_name"] for s in steps] != ["unnormalizer_processor", "device_processor"]:
        raise ValueError("Unexpected X-VLA decode pipeline")
    step = steps[0]
    if step["config"]["norm_map"]["ACTION"] != "MEAN_STD":
        raise ValueError("Expected X-VLA train-fit mean/std action statistics")
    state_path = checkpoint/step["state_file"]
    stats = load_file(state_path)
    probe = torch.arange(6, device="cuda", dtype=torch.float32).reshape(1, 1, 6)
    actual = post(probe).detach().cpu().numpy().reshape(6)
    expected = stats["action.mean"] + (stats["action.std"] + step["config"]["eps"]) * np.arange(6)
    if expected.shape != (6,) or not np.allclose(actual, expected, rtol=1e-5, atol=1e-5):
        raise ValueError("X-VLA decoder did not restore all six saved action axes in order")
    return dict(action_order=["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
                              "wrist_flex.pos", "wrist_roll.pos", "gripper.pos"],
                physical_units=["degrees"]*5 + ["gripper points 0–100"],
                postprocessor_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                normalization_state_sha256=hashlib.sha256(state_path.read_bytes()).hexdigest(),
                six_axis_normalization_probe="PASS")


def measure_compact(args):
    import av
    import torch
    from torch import nn

    torch.set_num_threads(1)
    saved = np.load(args.inputs, allow_pickle=False)
    rows, state, tasks = saved["rgb"], saved["state"], saved["tasks"]
    model = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    if model["model"] != args.model:
        raise ValueError("Wrong compact policy for benchmark")
    network = nn.Sequential(nn.Linear(model["dim"], 128), nn.ReLU(), nn.Linear(128, 128),
                            nn.ReLU(), nn.Linear(128, 96)).cuda().eval()
    network.load_state_dict(model["weights"], strict=True)
    x_mean, x_std, y_mean, y_std = [model[k].cuda() for k in ("xmean", "xs", "ym", "ys")]
    onehot = dict(zip(("red", "green", "blue"), np.eye(3, dtype=np.float32)))

    def infer(index, _):
        start = time.perf_counter()
        features = [av.VideoFrame.from_ndarray(view[-1], format="rgb24").reformat(
            width=8, height=6, format="rgb24", interpolation="AREA").to_ndarray() for view in rows[index]]
        rgb = np.asarray(features).reshape(-1).astype(np.float32) / 255
        values = [rgb, state[index, -1]]
        if args.model == "rgb_mlp_task":
            values.append(onehot[str(tasks[index]).split()[-2]])
        x = torch.from_numpy(np.concatenate(values)).cuda()
        with torch.inference_mode():
            actions = (network((x-x_mean)/x_std).reshape(-1, 6)*y_std + y_mean).cpu()
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        if not torch.isfinite(actions).all():
            raise ValueError("Invalid compact-policy output")
        return actions.tolist(), elapsed

    blue = [i for i, task in enumerate(tasks) if "blue basket" in str(task)]
    for i in range(10):
        infer(blue[i % len(blue)], "chunk")
    run = legacy_scheduler(args.live, infer, blue, state, args.duration, 16)
    sha = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    row = dict(model=args.model, checkpoint_step=args.step, weights_sha256=sha, mode="chunk",
               target_control_hz=30, speed=0.5, duration_s=args.duration, **run)
    filename(args, "scheduler").write_text(json.dumps([row], indent=2) + "\n")


def legacy_hash(stream):
    sha = hashlib.sha256()
    for block in iter(lambda: stream.read(1024*1024), b""):
        sha.update(block)
    return sha.hexdigest()


def filename(args, result):
    return args.output/f"{args.model}.{result}.json"


def legacy_scheduler(live, infer, blue, state, duration, horizon, mode="chunk"):
    sys.path[:0] = [str(live), str(live/"deps")]
    import stream_live as legacy

    # The deployed 60-Hz loop falls back to 50 Hz after slow work. Disable
    # that branch for this 30-Hz test so it cannot increase the target rate
    # and reach the packet cap before the requested wall duration.
    source = inspect.getsource(legacy.stream)
    fallback = "if hz == CONTROL_HZ and loop_work > 0.016:"
    if source.count(fallback) != 1:
        raise RuntimeError("The legacy cadence fallback changed")
    namespace = dict(vars(legacy), CONTROL_HZ=30)
    source = source.replace(fallback, "if hz > 50 and loop_work > 0.016:")
    exec(compile(source, inspect.getsourcefile(legacy.stream), "exec"), namespace)
    original_chunk = legacy.base.chunk
    if horizon != 50:
        def chunk(rows):
            if len(rows) != horizon:
                raise ValueError("Unexpected native policy horizon")
            return [legacy.base.vector(row, 6) for row in rows]
        legacy.base.chunk = chunk
    io = next(cls.FakeIO() for cls in vars(legacy).values() if isinstance(cls, type) and hasattr(cls, "FakeIO"))
    selected = iter(blue * (math.ceil(duration*30) + 1))
    selected_row = [blue[0]]

    def fresh():
        selected_row[0] = next(selected)
        return selected_row[0]

    io.fresh_frames = fresh
    io.checked = lambda: {"state": state[selected_row[0], -1, :6].tolist()}
    original_plan = io.plan

    def plan(target):
        packet = original_plan(target)
        # Legacy gap handling indexes the gripper by name. The original
        # test double uses a tuple while real controller plans use a mapping.
        packet["wire_raw"] = dict(zip(legacy.base.JOINTS, packet["wire_raw"], strict=True))
        return packet

    io.plan = plan
    pools = []

    def pool_factory(**options):
        pool = ThreadPoolExecutor(**options)
        pools.append(pool)
        return pool

    io.pool_factory = pool_factory
    used = set()
    phases = []

    def log(event):
        if event["event"] == "goal_transmitted":
            phase = event["model_phase"]
            phases.append((event["time_s"], phase))
            used.update((math.floor(phase), math.ceil(phase)))

    try:
        report = namespace["stream"](io, lambda i, _, origin, delay: infer(i, mode), log, duration, speed=.5)
    finally:
        legacy.base.chunk = original_chunk
        for pool in pools:
            pool.shutdown(wait=True, cancel_futures=True)
    elapsed = report["elapsed_s"]
    ready = report["ready"] is not None
    return dict(status=report["status"], reason=report["reason"], elapsed_s=elapsed, steps=report["steps"],
                ready=ready, transmission_hz=report["transmission_hz"],
                chunk_wait_s=report["chunk_wait_s"] if ready else None,
                unique_action_ids=len(used), unique_action_ids_per_s=len(used)/elapsed if ready and elapsed else None,
                first_goal_s=phases[0][0] if phases else None, last_goal_s=phases[-1][0] if phases else None,
                action_id_basis="distinct timeline IDs used as endpoints for actual mock goal interpolation",
                denominator="elapsed seconds from READY" if ready else "startup attempt ended before READY",
                simulation="legacy.stream with fake I/O, the native horizon, and 30-Hz cadence capped below the 50-Hz fallback")


def temporal_scheduler(infer, blue, duration):
    # ACT temporal ensembling emits one action after each fresh prediction.
    # Its stream holds the last result until the next 15-Hz action is ready.
    with ThreadPoolExecutor(max_workers=1) as pool:
        infer(blue[0], "temporal")
        started = time.monotonic()
        next_tick, next_action = started, started + 1/15
        steps, unique = 0, 1
        waits = []
        stalled = None
        future = pool.submit(infer, blue[1 % len(blue)], "temporal")
        while next_tick < started + duration:
            time.sleep(max(0, next_tick - time.monotonic()))
            now = time.monotonic()
            if now >= started + duration:
                break
            if now >= next_action:
                if future.done():
                    future.result()
                    unique += 1
                    next_action = now + 1/15 - .001
                    future = pool.submit(infer, blue[unique % len(blue)], "temporal")
                    if stalled is not None:
                        waits.append(now - stalled)
                        stalled = None
                elif stalled is None:
                    stalled = now
            steps += 1
            next_tick += 1/30
        elapsed = time.monotonic() - started
        if stalled is not None:
            waits.append(started + elapsed - stalled)
        future.result()
    return dict(status="RUN_COMPLETE", elapsed_s=elapsed, steps=steps, transmission_hz=steps/elapsed,
                chunk_wait_s=dict(max=max(waits, default=0), sum=sum(waits)), unique_action_ids=unique,
                unique_action_ids_per_s=unique/elapsed, denominator="elapsed seconds after first ready action",
                action_id_basis="one unique action per delivered ACT temporal prediction",
                simulation="30 Hz hold-last-action scheduler for native horizon 1")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--step", type=int)
    parser.add_argument("--model")
    parser.add_argument("--live", type=Path)
    parser.add_argument("--samples", type=int, default=42)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--temporal-legacy-only", action="store_true")
    parser.add_argument("--temporal-adapter", action="store_true")
    parser.add_argument("--act-prefixes", action="store_true")
    args = parser.parse_args()
    if args.act_prefixes and args.duration <= 0:
        parser.error("--act-prefixes requires a positive duration")
    if args.export:
        export(args.export, args.inputs)
    elif args.compact:
        measure_compact(args)
    else:
        measure(args)
