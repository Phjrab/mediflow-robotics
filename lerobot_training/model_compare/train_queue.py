"""Serial CUDA queue for official LeRobot trainers, with resume and provenance checks."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from prepare import digest, write

HERE = Path(__file__).resolve().parent
REPORT = HERE.parents[1]/"reports/model_compare"
BASES = {
    "smolvla": ("lerobot/smolvla_base", "5e8d12a6e2975b0e5e5fce7c8caf47c371d257b6"),
    "pi05": ("lerobot/pi05_base", "22c30e5b34f9e7ab738b1b34fc2fc5ecfe2d28f0"),
    "xvla": ("lerobot/xvla-base", "cdb7964e4fe842935d671bfab5a5ebe00a96648c"),
}
SCHEDULE = {"act": (100000, 8), "act_task": (100000, 8), "diffusion": (100000, 8),
            "smolvla": (20000, 4), "pi05": (20000, 1), "xvla": (20000, 1)}
CAMERAS = {"observation.images."+key: "observation.images.camera"+str(i)
           for i, key in enumerate(("end_effector", "ceiling_vertical", "ceiling_oblique"), 1)}


def update(status, name, state, step, eta="—"):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with status.open("a", encoding="utf-8") as f:
        f.write(f"\n- {now} · {name} · {state} · step {step} · ETA {eta}\n")


def run(args, name):
    import torch
    root = (args.datasets/("onehot" if name == "act_task" else "standard")).resolve()
    frozen = json.loads((REPORT/"split.json").read_bytes())
    if json.loads((root/"split.json").read_bytes()) != frozen:
        raise ValueError("Dataset does not match the frozen split")
    train = root/"train"
    meta = json.loads((train/"meta/info.json").read_bytes())
    stats = json.loads((train/"meta/stats.json").read_bytes())
    if (meta["total_episodes"], meta["total_frames"], stats["action"]["count"]) != (116, 53709, [53709]):
        raise ValueError("Incorrect training fold or normalization")
    if meta["features"]["observation.state"]["shape"] != ([9] if name == "act_task" else [6]):
        raise ValueError("Unexpected state contract")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    from huggingface_hub import snapshot_download
    steps, batch = SCHEDULE[name]
    model = name.removesuffix("_task")
    paths = args.outputs.resolve()
    if paths.is_relative_to(HERE.parents[1]):
        raise ValueError("Choose an output directory outside the repository")
    paths.mkdir(parents=True, exist_ok=True)
    directory = paths/name
    meta_path = paths/(name+".provenance.json")
    plan = dict(name=name, split_sha=digest(REPORT/"split.json"), train_stats_sha=digest(train/"meta/stats.json"),
                base=BASES.get(name), seed=101, steps=steps, batch=batch)
    plan = json.loads(json.dumps(plan))
    if meta_path.exists() and json.loads(meta_path.read_bytes()) != plan:
        raise ValueError("Existing run provenance differs")
    numeric = sorted((directory/"checkpoints").glob("[0-9]*"), key=lambda p: int(p.name))
    verified = [p for p in numeric if (p/"pretrained_model/train_config.json").is_file() and (p/"training_state").is_dir()]
    if directory.exists() and not (meta_path.exists() and verified):
        raise RuntimeError("Existing directory lacks a resumable checkpoint")
    write(meta_path, plan)
    command = [sys.executable, "-m", "lerobot.scripts.lerobot_train"]
    initial = int(verified[-1].name) if verified else 0
    if initial >= steps:
        update(args.status, name, "COMPLETE", initial)
        return
    if verified:
        command += [f"--config_path={verified[-1]}/pretrained_model/train_config.json", "--resume=true"]
    else:
        command += [f"--dataset.root={train}", "--dataset.repo_id=local/model-compare-train",
                    "--dataset.video_backend=pyav", f"--output_dir={directory}", f"--steps={steps}",
                    f"--batch_size={batch}", "--seed=101", "--save_freq=1000", "--log_freq=100",
                    "--num_workers=2", "--eval_steps=0", "--env_eval_freq=0",
                    "--policy.device=cuda", "--policy.push_to_hub=false", "--wandb.enable=false"]
        if name in BASES:
            repo, rev = BASES[name]
            model_dir = snapshot_download(repo, revision=rev)
            view_targets = {"smolvla": ("camera1","camera2","camera3"),
                            "pi05": ("left_wrist_0_rgb","base_0_rgb","right_wrist_0_rgb"),
                            "xvla": ("image","image2","image3")}[name]
            rename = {raw: "observation.images."+key for raw, key in zip(CAMERAS, view_targets, strict=True)}
            inputs = {key: {"type":"VISUAL","shape":[3,480,640]} for key in rename.values()}
            inputs["observation.state"] = {"type":"STATE","shape":[6]}
            command += [f"--policy.path={model_dir}", "--policy.input_features="+json.dumps(inputs),
                        "--rename_map="+json.dumps(rename)]
        else:
            command += [f"--policy.type={model}"]
        if name == "smolvla":
            command += ["--policy.use_amp=true", "--policy.freeze_vision_encoder=true",
                        "--policy.train_expert_only=true", "--policy.train_state_proj=true",
                        "--policy.chunk_size=50", "--policy.n_action_steps=50", "--policy.num_steps=10",
                        "--policy.optimizer_lr=0.0001", "--policy.scheduler_warmup_steps=1000",
                        "--policy.scheduler_decay_steps=30000", "--policy.scheduler_decay_lr=0.0000025"]
        if name == "pi05":
            command += ["--policy.dtype=bfloat16", "--policy.gradient_checkpointing=true"]
        if name == "xvla":
            from xvla_adapter import JOINTS
            names = meta["features"]["action"]["names"]
            if tuple(names) != tuple(JOINTS):
                raise ValueError("X-VLA joint order mismatch")
            command += ["--policy.action_mode=auto", "--policy.max_action_dim=20",
                        "--policy.num_image_views=3", "--policy.dtype=bfloat16",
                        "--policy.freeze_language_encoder=false", "--policy.freeze_vision_encoder=false",
                        "--policy.train_policy_transformer=true"]
    update(args.status, name, "RUNNING", initial)
    started = time.monotonic()
    last = initial
    with (paths/(name+".log")).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", env=dict(os.environ, PYTHONUNBUFFERED="1"))
        for line in process.stdout:
            log.write(line)
            log.flush()
            match = re.search(r"\bstep:([0-9.]+)([KkMm]?)\s", line)
            if match:
                last = int(float(match[1])*{"":1,"k":1000,"m":1000000}[match[2].lower()])
                if last > initial and last % 1000 == 0:
                    remaining = (time.monotonic()-started)*(steps-last)/(last-initial)
                    eta = datetime.now().astimezone()+timedelta(seconds=max(remaining,0))
                    update(args.status, name, "TRAINING", last, eta.isoformat(timespec="minutes"))
        if process.wait():
            raise RuntimeError(f"Trainer exited {process.returncode}, last reported step {last}")
    if not (directory/"checkpoints"/str(steps).zfill(6)/"pretrained_model/model.safetensors").exists():
        found = [p for p in (directory/"checkpoints").glob("[0-9]*") if int(p.name)==steps and
                 (p/"pretrained_model/model.safetensors").is_file()]
        if not found:
            raise RuntimeError("Final checkpoint missing")
    update(args.status, name, "COMPLETE", steps)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", type=Path)
    parser.add_argument("outputs", type=Path)
    parser.add_argument("--models", nargs="+", choices=list(SCHEDULE), default=list(SCHEDULE))
    parser.add_argument("--status", type=Path, default=REPORT/"STATUS.md")
    args = parser.parse_args()
    failed = []
    for name in args.models:
        try:
            run(args, name)
        except (RuntimeError, OSError, ValueError, ImportError) as exc:
            update(args.status, name, "BLOCKED "+type(exc).__name__, "—")
            print(name, exc, file=sys.stderr)
            failed.append(name)
    sys.exit(bool(failed))
