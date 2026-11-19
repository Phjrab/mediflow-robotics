"""Manifest-verified open-loop episode replay for the shared SO-101 IO path."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

from config_runtime import contract, load, path
import direct_live as base
from grasp_offset import GraspOffset
from gripper_safety import OpeningGuard
import live_start

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_MANIFEST = Path.home() / "so101_replay" / "replay_manifest.json"
PUBLIC_REFERENCE = Path(__file__).with_name("assets") / "grasp_motion_reference.json"
FPS = 30
MAX_DURATION_S = 180
ALLOWED_SPEEDS = (.33, .5)
COLORS = ("red", "green", "blue")


def load_episode(manifest_path, episode, *, calibration_sha256):
    """Verify the selected private file before returning any source rows."""
    if type(episode) is not int or not 0 <= episode < 137:
        raise ValueError("Replay episode must be a merged ID from 0 to 136")
    manifest_path = Path(manifest_path).resolve(strict=True)
    document = manifest_path.read_bytes()
    manifest_digest = hashlib.sha256(document).hexdigest()
    trusted = json.loads(PUBLIC_REFERENCE.read_text(encoding="utf-8"))["replay_manifest_sha256"]
    if manifest_digest != trusted:
        raise ValueError("Replay manifest SHA-256 does not match the installed reference")
    manifest = json.loads(document)
    if (manifest["schema_version"], manifest["fps"], manifest["joints"], manifest["calibration_sha256"]) != (
            1, FPS, list(base.JOINTS), calibration_sha256):
        raise ValueError("Replay manifest joint, FPS or calibration contract mismatch")
    entry = manifest["episodes"][str(episode)]
    expected_name = f"private/ep_{episode:03d}.json"
    if entry["file"] != expected_name or type(entry["episode"]) is not int or entry["episode"] != episode:
        raise ValueError("Replay manifest entry identity mismatch")
    recording_path = (manifest_path.parent / expected_name).resolve(strict=True)
    if recording_path.parent != manifest_path.parent / "private":
        raise ValueError("Replay source escaped its private directory")
    raw = recording_path.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != entry["sha256"]:
        raise ValueError("Replay action file SHA-256 mismatch")
    recording = json.loads(raw)
    color = entry["color"]
    if color not in COLORS or recording["task"] != f"Put medicine bottle A into the {color} basket":
        raise ValueError("Replay task mismatch")
    for key in ("schema_version", "fps", "joints", "calibration_sha256"):
        if recording[key] != manifest[key]:
            raise ValueError(f"Replay source contract mismatch: {key}")
    if any(recording[key] != entry[key] for key in ("episode", "source", "source_episode")):
        raise ValueError("Replay source episode identity mismatch")
    actions = recording["actions"]
    if type(entry["frames"]) is not int or len(actions) != entry["frames"] or not 1 <= len(actions) <= 3000:
        raise ValueError("Replay source length mismatch")
    for row in actions:
        if not isinstance(row, list) or len(row) != 6 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row) or not 0 <= row[-1] <= 100:
            raise ValueError("Invalid replay action row")
    return recording, dict(episode=episode, source=entry["source"], source_episode=entry["source_episode"],
                           source_revision=entry["revision"], action_file_sha256=file_sha,
                           replay_manifest_sha256=manifest_digest)


def validate_options(speed, duration):
    if type(speed) not in (int, float) or speed not in ALLOWED_SPEEDS:
        raise ValueError("Replay speed must be .33 or .5")
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION_S:
        raise ValueError("Replay duration must be within (0, 180] seconds")


def replay(io, actions, log, *, speed=.33, duration=180, monitor=None, recorder=None):
    """Send each source row once at most, preserving order even after a slow tick."""
    validate_options(speed, duration)
    if not actions:
        raise ValueError("Replay requires a nonempty verified action sequence")
    config = load()
    clock, pause = io.clock, io.pause
    opening = OpeningGuard(config["gripper"])
    lateral = GraspOffset(config["lateral_correction"])
    step = 0
    period = 1 / (FPS * speed)
    started = None
    reason = None
    hold = {"status": "NOT_NEEDED"}
    clip_counts = dict.fromkeys(base.JOINTS, 0)

    try:
        startup = live_start.prepare(io, config["home"]["state"], log)
        io.motion_ready = True
        if hasattr(io, "policy_active"):
            io.policy_active = True
        started = clock()
        deadline = started + duration
        next_send = started
        feedback_budget = 0.0
        lookahead = math.ceil(lateral.settings["lookahead_wall_s"] / period)
        log(dict(event="replay_ready", speed=speed, fps=FPS, duration_limit_s=duration,
                 source_frames=len(actions), expected_last_frame_s=(len(actions)-1)*period,
                 startup=startup))

        for index, action in enumerate(actions):
            if next_send >= deadline:
                reason = "duration_limit"
                break
            delay = next_send - feedback_budget - clock()
            if delay > 0:
                pause(delay)
            if monitor is not None and monitor.hold_requested():
                raise base.Stop("Episode replay stop requested by operator")
            if clock() >= deadline:
                reason = "duration_limit"
                break
            feedback_started = clock()
            sample = io.checked()
            feedback_budget = min(period, clock()-feedback_started)
            now = clock()
            future = actions[index+1:index+1+lookahead]
            target, offset = lateral.apply(action, sample["state"], now, future, FPS*speed)
            target[-1] = opening.apply(target[-1], sample["state"][-1], sample["loads"]["gripper"], now)
            plan = io.plan(target)
            delay = next_send - clock()
            if delay > 0:
                pause(delay)
            if monitor is not None and monitor.hold_requested():
                raise base.Stop("Episode replay stop requested by operator")
            if clock() >= deadline:
                reason = "duration_limit"
                break
            io.send(plan["wire_raw"])
            sent = clock()
            step += 1
            for joint in plan.get("clipped_joints", ()):
                clip_counts[joint] += 1
            row = dict(event="recorded_goal_transmitted", step=step, time_s=sent-started,
                       source_frame=index, source_action=list(action), model=list(action),
                       model_phase=index, playback_s=index/FPS, feedback=sample,
                       protected_action=target, lateral_correction=offset, **plan)
            log(row)
            if recorder is not None:
                grasp = recorder.goal(plan["wire_normalized"][-1], row, time_s=sent-started, monotonic=sent)
                if grasp is not None:
                    log(dict(event="grasp_event", **grasp))
            if monitor is not None:
                monitor.status("moving", step=step, sample=sample, model=action, sent=plan["wire_normalized"])
            if recorder is not None or monitor is not None:
                frames = io.camera_frames()
                if frames is not None and recorder is not None:
                    recorder.capture(frames, sample, time_s=clock()-started, monotonic=clock())
                if frames is not None and monitor is not None and step % 3 == 1:
                    monitor.capture(frames, sample["state"])
            # Stretch the schedule after a late send, without skipping or catching up rows.
            next_send = sent + period
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except Exception as exc:
        reason = str(exc)
    finally:
        elapsed = 0 if started is None else clock()-started
        if io.goal_attempted:
            hold = base.try_hold(io)
            if hasattr(io, "confirm_thermal_stop"):
                try:
                    io.confirm_thermal_stop()
                except (KeyboardInterrupt, Exception) as exc:
                    log(dict(event="replay_thermal_recheck_error", reason=str(exc)))
    source_exhausted = step == len(actions)
    if reason is None and hold["status"] == "HOLD_NOT_CONFIRMED":
        reason = "hold_not_confirmed"
    stopped = reason in ("Ctrl+C", "duration_limit")
    return dict(mode="episode_replay", status="STOPPED" if stopped else "HOLD" if reason else "RUN_COMPLETE",
                reason=reason, speed=speed, fps=FPS, steps=step, source_frames=len(actions),
                replay_end="source_exhausted" if source_exhausted else "duration_limit" if reason == "duration_limit" else "interrupted",
                last_source_frame=step-1 if step else None, elapsed_s=elapsed, duration_limit_s=duration,
                nominal_last_frame_s=(len(actions)-1)*period, hold=hold,
                range_clip_counts=clip_counts, gripper_opening_protection=opening.summary(),
                lateral_correction=lateral.summary(), policy_arrival_gate_enabled=False, policy_assist_enabled=False,
                model_used=False, task_success="OPERATOR_REVIEW_REQUIRED")


def deny_serial(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        name = os.fsdecode(args[0])
        if name.startswith(("/dev/tty", "/dev/serial/")) or name == "/dev/serial":
            raise RuntimeError("Episode replay dry-run blocks physical serial")


def replay_context(config, *, dry_run):
    context = dict(home=config["home"], contract=contract(config), stop_inventory={}, files={})
    if not dry_run:
        calibration = path(config["calibration"])
        digest = hashlib.sha256(calibration.read_bytes()).hexdigest()
        if digest != config["calibration_sha256"]:
            raise ValueError("Replay calibration SHA-256 mismatch")
        context.update(calibration=calibration, files={str(calibration): digest})
    return context


def wait_for_initial_frames(io, camera_source, monitor, evidence):
    """Use the shared brightness gate before any replay or startup goals."""
    started = io.clock()
    deadline = started + evidence["timeout_s"]
    expected = set(evidence["expected_roles"])
    dark = set()
    evidence.update(status="checking", attempts=0, last_valid_roles=[])
    try:
        while io.clock() < deadline:
            if monitor.hold_requested():
                raise base.Stop("Episode replay stop requested by operator")
            sample = io.checked()
            if io.clock() >= deadline:
                break
            evidence["attempts"] += 1
            frames = camera_source.fresh_frames()
            dark.update(camera_source.dark_cameras)
            evidence["last_valid_roles"] = sorted(frames or {})
            if frames is not None and set(frames) == expected and io.clock() < deadline:
                evidence["status"] = "passed"
                return frames, sample
            remaining = deadline - io.clock()
            if remaining > 0:
                io.pause(min(.02, remaining))
        evidence.update(status="timed_out", reason="camera_near_black" if dark else "missing_or_incomplete")
        raise base.Stop(f"READY 전 카메라 첫 유효 프레임 {evidence['timeout_s']:g}초 초과")
    except (KeyboardInterrupt, Exception) as exc:
        if evidence["status"] == "checking":
            evidence.update(status="interrupted", reason="Ctrl+C" if isinstance(exc, KeyboardInterrupt) else str(exc))
        raise
    finally:
        dark.update(camera_source.dark_cameras)
        evidence.update(elapsed_s=io.clock()-started, dark_roles_seen=sorted(dark))


def run_episode(episode, duration, speed, *, dry_run, manifest_path=None):
    """CLI integration point. The dry-run branch opens cameras and never a motor bus."""
    validate_options(speed, duration)
    if type(dry_run) is not bool:
        raise ValueError("Replay execution mode must be explicit")
    if not dry_run and os.environ.get("SO101_DIRECT_OPERATOR") != "1":
        raise ValueError("Physical replay requires shared operator authorization from the CLI")
    cfg = load()
    recording, provenance = load_episode(manifest_path or os.environ.get("SO101_REPLAY_MANIFEST", DEFAULT_DATA_MANIFEST),
                                         episode, calibration_sha256=cfg["calibration_sha256"])
    if dry_run:
        sys.addaudithook(deny_serial)
    from grasp_recorder import GraspRecorder
    from run_exit_report import record_run_directory
    from stream_live import device_class
    from vla_monitor import Monitor

    folder = ROOT / "runs" / f"{'DRY' if dry_run else 'LIVE'}_EPISODE_REPLAY_{episode:03d}_{time.time_ns()}"
    folder.mkdir(parents=True)
    record_run_directory(folder)
    camera_source = io = monitor = recorder = None
    result = dict(status="HOLD", steps=0, mode="episode_replay", model_used=False,
                  replay_end="not_started")
    camera_preflight = dict(status="not_started", source="fresh_frames",
                            timeout_s=cfg["startup"]["camera_first_frame_s"], expected_roles=sorted(cfg["cameras"]))
    with (folder / "steps.jsonl").open("x", encoding="utf-8", buffering=1) as trace:
        def log(row):
            trace.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")

        try:
            context = replay_context(cfg, dry_run=dry_run)
            monitor = Monitor(folder.name, recording["task"], base.JOINTS, controls=not dry_run)
            monitor.status("connecting", force=True)
            if dry_run:
                from startup_simulation import SimulatedArm

                io = SimulatedArm(context["home"]["state"], {}, log, "camera_dry", real_time=True)
                io.connect()
                camera_source = device_class()(context, log, folder)
                camera_source.open_cameras(io.checked)
                io.camera_frames = camera_source.camera_frames
                io.cameras_stats = camera_source.cameras_stats
            else:
                io = device_class()(context, log, folder)
                io.connect()
            try:
                frames, sample = wait_for_initial_frames(io, camera_source if dry_run else io, monitor, camera_preflight)
            finally:
                log(dict(event="replay_camera_preflight", **camera_preflight))
            monitor.capture(frames, sample["state"], preview=True)
            recorder = GraspRecorder(folder, base.JOINTS, clock=io.clock)
            log(dict(event="episode_replay_source", **provenance))
            result = replay(io, recording["actions"], log, speed=speed, duration=duration,
                            monitor=monitor, recorder=recorder)
        except (KeyboardInterrupt, Exception) as exc:
            result.update(status="STOPPED" if isinstance(exc, KeyboardInterrupt) else "HOLD",
                          reason="Ctrl+C" if isinstance(exc, KeyboardInterrupt) else str(exc))
            if io is not None and io.goal_attempted:
                result["hold"] = base.try_hold(io)
        finally:
            errors = []
            if recorder is not None:
                try:
                    recorder.close(reason=result.get("reason") or "run_end")
                    result["grasp_recording"] = recorder.summary()
                except Exception as exc:
                    errors.append(f"recorder: {exc}")
            resource = camera_source if dry_run else io
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    errors.append(str(exc))
                errors.extend(resource.cleanup_errors)
                serial = getattr(getattr(getattr(resource, "bus", None), "port_handler", None), "ser", None)
                result["port_closed"] = not bool(getattr(serial, "is_open", False))
            if io is not None:
                result["camera"] = io.cameras_stats.summary()
                result["thermal_protection"] = io.thermal_window.summary()
                if not dry_run:
                    result.update(thermal_diagnostics=io.thermal_diagnostics.summary(),
                                  communication=io.communication.summary(), thermal_recheck=io.thermal_recheck)
            result.update(replay_episode=episode, provenance=provenance, output=str(folder), dry_run=dry_run, cleanup_errors=errors,
                          camera_preflight=camera_preflight,
                          serial_open_blocked=dry_run, configuration_register_writes=0,
                          camera_scope="physical cameras; simulated motion" if dry_run else "physical cameras and motion")
            if dry_run:
                result["physical_motor_writes"] = 0
                result["simulated_start_writes"] = io.start_writes if io is not None else []
            else:
                result["torque_register_writes"] = sum(getattr(io, key, 0) for key in ("torque_on_writes", "torque_off_writes"))
            if monitor is not None:
                monitor.finish(result["status"], result.get("reason"), result["steps"], result.get("hold"))
            (folder / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "RUN_COMPLETE" and not errors and result.get("port_closed") else 1
