"""Serial CUDA queue for official LeRobot trainers, with resume and provenance checks."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from prepare import digest, write
from training_worker import validation_recorded

HERE = Path(__file__).resolve().parent
REPORT = HERE.parents[1]/"reports/model_compare"
BASES = {
    "smolvla": ("lerobot/smolvla_base", "5e8d12a6e2975b0e5e5fce7c8caf47c371d257b6"),
    "pi05": ("lerobot/pi05_base", "22c30e5b34f9e7ab738b1b34fc2fc5ecfe2d28f0"),
    "xvla": ("lerobot/xvla-base", "cdb7964e4fe842935d671bfab5a5ebe00a96648c"),
}
SCHEDULE = {"smolvla": (20000, 4), "act_task": (100000, 8), "act": (100000, 8),
            "diffusion": (100000, 8), "xvla": (20000, 1), "pi05": (20000, 1)}
DEFAULT_MODELS = tuple(name for name in SCHEDULE if name != "pi05")
CAMERAS = {"observation.images."+key: "observation.images.camera"+str(i)
           for i, key in enumerate(("end_effector", "ceiling_vertical", "ceiling_oblique"), 1)}


def update(status, name, state, step, eta="—"):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with status.open("a", encoding="utf-8") as f:
        f.write(f"\n- {now} · {name} · {state} · step {step} · ETA {eta}\n")


def checkpoints(directory, expected_plan):
    verified = []
    for path in (directory/"checkpoints").glob("[0-9]*"):
        if not path.name.isdecimal():
            continue
        state = path/"training_state"
        required = ("queue_complete", "training_step.json", "optimizer_state.safetensors",
                    "optimizer_param_groups.json", "rng_state.safetensors")
        if not all((state/key).is_file() for key in required):
            continue
        if not all((path/"pretrained_model"/key).is_file() for key in
                   ("config.json", "train_config.json", "model.safetensors",
                    "policy_preprocessor.json", "policy_postprocessor.json")):
            continue
        recorded = json.loads((state/"training_step.json").read_bytes())
        if (recorded["step"] != int(path.name) or recorded.get("batch_size") != expected_plan["batch"]
                or recorded.get("dp_world_size") != 1 or recorded.get("grad_accum_steps") != 1):
            raise ValueError("Checkpoint metadata disagrees with the queue")
        verified.append(path)
    return sorted(verified, key=lambda path: int(path.name))


def execute(command, root, metrics, log_path, deadline, steps, initial=0, probe_steps=0, probe_result=None,
            checkpoint=None, lock_fd=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 75
    reserve = min(120.0, remaining * 0.2)
    worker = [sys.executable, str(HERE/"training_worker.py"), f"--dataset={root}", f"--metrics={metrics}",
              f"--soft-deadline={deadline - reserve}", f"--hard-deadline={deadline}",
              f"--target={steps}", f"--initial={initial}"]
    if probe_steps:
        worker.extend((f"--probe-steps={probe_steps}", f"--probe-result={probe_result}"))
    if checkpoint:
        worker.append(f"--checkpoint={checkpoint}")
    inherited = {"pass_fds": (lock_fd,)} if os.name == "posix" and lock_fd is not None else {}
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(worker + command, stdout=log, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, PYTHONUNBUFFERED="1"), start_new_session=os.name == "posix",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   **inherited)
        timed_out = False
        try:
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            timed_out = os.name == "posix" and code == -signal.SIGALRM
            return 75 if timed_out else code
        except subprocess.TimeoutExpired:
            timed_out = True
            return 75
        finally:
            if timed_out or process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()


def run_model(args, name, deadline, lock_fd):
    import torch
    root = (args.datasets/("onehot" if name == "act_task" else "standard")).resolve()
    frozen = json.loads((REPORT/"split.json").read_bytes())
    if json.loads((root/"split.json").read_bytes()) != frozen:
        raise ValueError("Dataset does not match the frozen split")
    held = [row for row in frozen["episodes"] if row["fold"] == "eval"]
    val_meta = json.loads((root/"eval/meta/info.json").read_bytes())
    if (len(held), val_meta["total_episodes"], val_meta["total_frames"]) != (21, 21, sum(r["frames"] for r in held)):
        raise ValueError("Incorrect holdout fold")
    train = root/"train"
    meta = json.loads((train/"meta/info.json").read_bytes())
    stats = json.loads((train/"meta/stats.json").read_bytes())
    if (meta["total_episodes"], meta["total_frames"], stats["action"]["count"]) != (116, 53709, [53709]):
        raise ValueError("Incorrect training fold or normalization")
    if meta["features"]["observation.state"]["shape"] != ([9] if name == "act_task" else [6]):
        raise ValueError("Unexpected state contract")
    if name == "xvla":
        verify_xvla_axes(meta, val_meta)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    if version("lerobot") != "0.6.2":
        raise RuntimeError("Expected LeRobot 0.6.2")
    steps, batch = SCHEDULE[name]
    model = name.removesuffix("_task")
    paths = args.outputs.resolve()
    if paths.is_relative_to(HERE.parents[1]):
        raise ValueError("Choose an output directory outside the repository")
    paths.mkdir(parents=True, exist_ok=True)
    directory = paths/name
    meta_path = paths/(name+".provenance.json")
    plan = dict(name=name, split_sha=digest(REPORT/"split.json"), train_stats_sha=digest(train/"meta/stats.json"),
                base=BASES.get(name), seed=101, steps=steps, batch=batch, instrumentation=1)
    plan = json.loads(json.dumps(plan))
    if meta_path.exists() and json.loads(meta_path.read_bytes()) != plan:
        raise ValueError("Existing run provenance differs")
    if directory.exists() and not meta_path.is_file():
        raise RuntimeError("Existing directory lacks training provenance")
    verified = checkpoints(directory, plan)
    initial = int(verified[-1].name) if verified else 0
    if initial >= steps and validation_recorded(directory/"validation_metrics.csv", steps) and not args.probe_only:
        update(args.status, name, "COMPLETE", initial)
        return
    command = []
    if verified:
        command += [f"--config_path={verified[-1]}/pretrained_model/train_config.json", "--resume=true",
                    f"--output_dir={directory}", f"--dataset.root={train}"]
    else:
        command += [f"--dataset.root={train}", "--dataset.repo_id=local/model-compare-train",
                    "--dataset.video_backend=pyav", f"--steps={steps}",
                    f"--batch_size={batch}", "--seed=101", "--save_freq=1000", "--log_freq=100",
                    "--num_workers=2", "--eval_steps=0", "--env_eval_freq=0",
                    "--policy.device=cuda", "--policy.push_to_hub=false", "--wandb.enable=false"]
        if name in BASES:
            repo, rev = BASES[name]
            view_targets = {"smolvla": ("camera1","camera2","camera3"),
                            "pi05": ("left_wrist_0_rgb","base_0_rgb","right_wrist_0_rgb"),
                            "xvla": ("image","image2","image3")}[name]
            rename = {raw: "observation.images."+key for raw, key in zip(CAMERAS, view_targets, strict=True)}
            inputs = {key: {"type":"VISUAL","shape":[3,480,640]} for key in rename.values()}
            inputs["observation.state"] = {"type":"STATE","shape":[6]}
            command += [f"--base-repo={repo}", f"--base-revision={rev}",
                        "--policy.input_features="+json.dumps(inputs), "--rename_map="+json.dumps(rename)]
        else:
            command += [f"--policy.type={model}"]
        if name == "smolvla":
            command += ["--policy.use_amp=true", "--accelerator.mixed_precision=bf16",
                        "--policy.freeze_vision_encoder=true",
                        "--policy.train_expert_only=true", "--policy.train_state_proj=true",
                        "--policy.chunk_size=50", "--policy.n_action_steps=50", "--policy.num_steps=10",
                        "--policy.optimizer_lr=0.0001", "--policy.scheduler_warmup_steps=1000",
                        "--policy.scheduler_decay_steps=30000", "--policy.scheduler_decay_lr=0.0000025"]
        if name == "pi05":
            command += ["--policy.dtype=bfloat16", "--policy.gradient_checkpointing=true"]
        if name == "xvla":
            command += ["--policy.action_mode=auto", "--policy.max_action_dim=20",
                        "--policy.num_image_views=3", "--policy.dtype=bfloat16",
                        "--policy.freeze_language_encoder=false", "--policy.freeze_vision_encoder=false",
                        "--policy.train_policy_transformer=true"]
    probe_path = paths/(name+".probe.json")
    prior_probe = json.loads(probe_path.read_bytes()) if probe_path.is_file() else {}
    matching_probe = (prior_probe.get("provenance") == plan and len(prior_probe.get("steps", ())) == args.probe_steps
                      and prior_probe.get("gpu") == torch.cuda.get_device_name()
                      and prior_probe.get("gpu_bytes") == torch.cuda.get_device_properties(0).total_memory)
    if args.probe_only or (not verified and not matching_probe):
        if verified:
            raise ValueError("Probe an existing run with a separate output root")
        update(args.status, name, "PROBING", 0)
        with tempfile.TemporaryDirectory(prefix=f".{name}-probe-", dir=paths) as tmp:
            tmp = Path(tmp)
            result = tmp/"result.json"
            code = execute(command + [f"--output_dir={tmp/'trial'}"], root, tmp/"trial",
                           paths/(name+".probe.log"), deadline, steps,
                           probe_steps=args.probe_steps, probe_result=result, lock_fd=lock_fd)
            if code or not result.is_file():
                write_atomic(probe_path, dict(status="FAILED", exit_code=code, provenance=plan))
                raise RuntimeError(f"GPU probe did not finish (exit {code}). See {name}.probe.log")
            probe = json.loads(result.read_bytes())
            write_atomic(probe_path, {**probe, "status": "COMPLETE", "provenance": plan})
        update(args.status, name, "PROBED", args.probe_steps)
        prior_probe = probe
        matching_probe = True
    if args.probe_only:
        return
    write_atomic(meta_path, plan)
    if not verified:
        # A failed child can leave metrics before its first checkpoint. Use a
        # preserved attempt directory so LeRobot can start a fresh run.
        if directory.exists():
            retry = Path(tempfile.mkdtemp(prefix=f"{name}.attempt-", dir=paths))
            directory.rename(retry/"run")
        command.append(f"--output_dir={directory}")
    eta = "—"
    if matching_probe:
        seconds = prior_probe["estimated_train_seconds"] * (steps - initial) / steps
        eta = (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat(timespec="minutes")
        eta += " (training updates only)"
    update(args.status, name, "RUNNING", initial, eta)
    checkpoint = verified[-1]/"pretrained_model" if verified else None
    code = execute(command, root, directory, paths/(name+".log"), deadline, steps, initial,
                   checkpoint=checkpoint, lock_fd=lock_fd)
    current = checkpoints(directory, plan)
    saved = int(current[-1].name) if current else 0
    if code == 75:
        update(args.status, name, "TIME_LIMIT", saved)
        return
    if code:
        raise RuntimeError(f"Trainer exited {code}, last saved step {saved}. See {name}.log")
    if saved != steps or not validation_recorded(directory/"validation_metrics.csv", steps):
        raise RuntimeError("Final checkpoint or full holdout metrics missing")
    update(args.status, name, "COMPLETE", steps)


def write_atomic(path, data):
    temporary = path.with_suffix(".tmp")
    write(temporary, data)
    temporary.replace(path)


def verify_xvla_axes(*metadata):
    from xvla_adapter import JOINTS

    for fold in metadata:
        for key in ("action", "observation.state"):
            feature = fold["features"][key]
            if tuple(feature["names"]) != JOINTS or feature["shape"] != [len(JOINTS)]:
                raise ValueError(f"X-VLA {key} joint order or shape mismatch")


@contextmanager
def single_gpu_lock():
    # This path is shared by queue invocations with different output roots or models.
    directory = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    with (directory/"mediflow-model-compare.cuda.lock").open("a+b") as lock:
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another model-compare queue holds the GPU lock") from exc
        yield lock.fileno()


def run(args, name, lock_fd):
    paths = args.outputs.resolve()
    ledger = paths/(name+".time.json")
    record = json.loads(ledger.read_bytes()) if ledger.is_file() else {}
    spent = record.get("seconds", 0.0)
    if "started_at" in record:
        # An interrupted controller may have left the worker active. Charge
        # the elapsed part of that worker's lease before issuing a new one.
        spent += min(record["lease_seconds"], max(0.0, time.time() - record["started_at"]))
    remaining = args.max_hours * 3600 - spent
    if remaining <= 0:
        update(args.status, name, "TIME_BUDGET_EXHAUSTED", "—")
        return
    started = time.monotonic()
    write_atomic(ledger, dict(seconds=spent, started_at=time.time(), lease_seconds=remaining))
    try:
        run_model(args, name, started + remaining, lock_fd)
    finally:
        write_atomic(ledger, dict(seconds=spent + min(remaining, time.monotonic() - started)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", type=Path)
    parser.add_argument("outputs", type=Path)
    parser.add_argument("--models", nargs="+", choices=list(SCHEDULE), default=DEFAULT_MODELS)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--probe-steps", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()
    if not 0 < args.max_hours <= 8:
        parser.error("--max-hours must be in (0, 8]")
    if args.outputs.resolve().is_relative_to(HERE.parents[1]):
        parser.error("Choose an output directory outside the repository")
    args.outputs.mkdir(parents=True, exist_ok=True)
    if args.status is None:
        args.status = args.outputs/"STATUS.md"
    args.status.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    try:
        with single_gpu_lock() as lock_fd:
            for name in args.models:
                try:
                    run(args, name, lock_fd)
                except (RuntimeError, OSError, ValueError, ImportError) as exc:
                    update(args.status, name, "BLOCKED "+type(exc).__name__, "—")
                    print(name, exc, file=sys.stderr)
                    failed.append(name)
    except (RuntimeError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    sys.exit(bool(failed))
