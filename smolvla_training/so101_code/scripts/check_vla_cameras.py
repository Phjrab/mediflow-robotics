#!/usr/bin/env python3
"""Camera-only Stage 1 check. No robot/serial imports, services, or motor commands.

Default: validate YAML without opening any device. Explicit --capture: one frame
per verified by-id path, in bounded child processes. No images are saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing as mp
from pathlib import Path
import re
import stat
import sys
import time

import yaml

ROLES = ("wrist", "top", "oblique")
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs/vla_cameras.yaml"


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys, including duplicates hidden in YAML merges."""


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ConfigError("duplicate YAML key")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise ConfigError("YAML mapping keys must be scalar") from exc
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ConfigError(f"{label}: expected exactly {', '.join(keys)}")


def validate_config(data):
    exact_keys(data, ("schema_version", "cameras"), "root")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigError("schema_version must be integer 1")
    cameras = data["cameras"]
    exact_keys(cameras, ROLES, "cameras")
    identities = set()
    for role in ROLES:
        cfg = cameras[role]
        exact_keys(cfg, ("device", "width", "height", "fps"), role)
        for key, maximum in (("width", 8192), ("height", 8192), ("fps", 240)):
            if type(cfg[key]) is not int or not 1 <= cfg[key] <= maximum:
                raise ConfigError(f"{role}.{key}: positive integer <= {maximum} required")
        device = cfg["device"]
        if device is None:
            continue
        if not isinstance(device, str) or not re.fullmatch(r"/dev/v4l/by-id/[A-Za-z0-9_.:+-]+", device):
            raise ConfigError(f"{role}.device: verified /dev/v4l/by-id path or null required")
        if Path(device).name in (".", ".."):
            raise ConfigError(f"{role}.device: invalid path")
        # Two video interfaces of the same USB camera are not two camera roles.
        identity = re.sub(r"-video-index\d+$", "", device)
        if identity in identities:
            raise ConfigError("duplicate camera identity")
        identities.add(identity)
    return data


def load_config(path):
    return validate_config(yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader))


def resolve_device(device):
    """Metadata only; does not open the device. Reject by-id aliases to tty/files."""
    path = Path(device)
    if not path.is_symlink():
        raise ConfigError("by-id identity must be a symlink")
    target = path.resolve(strict=True)
    if not re.fullmatch(r"/dev/video\d+", str(target)):
        raise ConfigError("by-id target is not a V4L2 video node")
    info = target.stat()
    if not stat.S_ISCHR(info.st_mode):
        raise ConfigError("V4L2 target is not a character device")
    physical = None
    sys_device = Path("/sys/class/video4linux") / target.name / "device"
    if sys_device.exists():
        for parent in (sys_device.resolve(), *sys_device.resolve().parents):
            if (parent / "idVendor").exists() and (parent / "idProduct").exists():
                physical = str(parent)
                break
    return {"target": str(target), "rdev": info.st_rdev, "physical": physical}


def resolve_all(config, resolver=resolve_device):
    resolved, targets, physicals = {}, set(), set()
    for role in ROLES:
        device = config["cameras"][role]["device"]
        if device is None:
            raise ConfigError(f"{role}: identity unverified; capture NOT_RUN")
        identity = resolver(device)
        if identity["rdev"] in targets:
            raise ConfigError("duplicate resolved video device")
        targets.add(identity["rdev"])
        physical = identity["physical"]
        if physical is not None:
            if physical in physicals:
                raise ConfigError("duplicate physical USB camera")
            physicals.add(physical)
        resolved[role] = identity
    return resolved


def positive_finite(value):
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def capture_one(cfg, cv):
    # Mirrors existing preview's OpenCV/V4L2 backend but does NOT convert to int,
    # retry indefinitely, import the web service, or touch Astra bridge services.
    cap = cv.VideoCapture(cfg["device"], cv.CAP_V4L2)
    try:
        if not cap.isOpened():
            raise RuntimeError("camera open failed")
        cap.set(cv.CAP_PROP_FRAME_WIDTH, cfg["width"])
        cap.set(cv.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        cap.set(cv.CAP_PROP_FPS, cfg["fps"])
        started = time.monotonic()
        ok, frame = cap.read()
        received = datetime.now(timezone.utc).isoformat()
        elapsed = time.monotonic() - started
        if not ok or frame is None or frame.size == 0:
            raise RuntimeError("camera frame read failed")
        if frame.ndim != 3 or frame.shape[2] != 3 or str(frame.dtype) != "uint8":
            raise RuntimeError("expected uint8 BGR frame with three channels")
        width, height = int(frame.shape[1]), int(frame.shape[0])
        # A diagnostic heuristic, not an exposure/liveness guarantee.
        black = bool((frame <= 5).mean() >= 0.99)
        size_ok = (width, height) == (cfg["width"], cfg["height"])
        return {
            "status": "PASS" if size_ok and not black else "FAIL",
            "frames_read": 1, "actual_width": width, "actual_height": height,
            "resolution_matches": size_ok, "black_frame": black,
            "black_rule": "at least 99% of channel values <= 5 (uint8)",
            "configured_fps": cfg["fps"],
            "backend_reported_fps": positive_finite(cap.get(cv.CAP_PROP_FPS)),
            "measured_fps": None, "sensor_capture_timestamp": None,
            "host_received_at_utc": received, "read_elapsed_seconds": elapsed,
            "liveness_verified": False,
        }
    finally:
        cap.release()


def capture_worker(connection, cfg, expected):
    try:
        # Recheck hotplug identity immediately before opening (not a hotplug lock).
        if resolve_device(cfg["device"]) != expected:
            raise ConfigError("device identity changed before capture")
        import cv2  # lazy: config validation never imports a device backend
        connection.send(capture_one(cfg, cv2))
    except Exception as exc:
        # Do not include backend exception strings containing local credentials/paths.
        connection.send({"status": "FAIL", "error_type": type(exc).__name__})
    finally:
        connection.close()


def bounded_capture(cfg, expected, timeout, context=None):
    context = context or mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=capture_worker, args=(writer, cfg, expected), daemon=True)
    try:
        process.start()
    except Exception as exc:
        reader.close()
        writer.close()
        return {"status": "FAIL", "error": "camera worker could not start", "error_type": type(exc).__name__}
    writer.close()
    try:
        if not reader.poll(timeout):
            return {"status": "FAIL", "error": "camera capture timeout", "frames_read": 0}
        try:
            return reader.recv()
        except EOFError:
            return {"status": "FAIL", "error": "camera worker exited without a result"}
    finally:
        reader.close()
        process.join(0.2)
        if process.is_alive():
            process.terminate()
            process.join(0.5)
        if process.is_alive():
            process.kill()
            process.join(0.5)


def check(config, capture=False, timeout=10, resolver=resolve_device, runner=bounded_capture):
    validate_config(config)
    report = {
        "schema_version": 1, "mode": "capture" if capture else "validate",
        "schema_valid": True, "hardware_verified": False,
        "cameras": {role: {"status": "NOT_RUN", "configured_fps": config["cameras"][role]["fps"],
                           "identity_configured": config["cameras"][role]["device"] is not None}
                    for role in ROLES},
    }
    if not capture:
        report["status"] = "NOT_RUN"
        report["reason"] = "schema only; camera identities and frames are not verified"
        return report, 0
    # Resolve ALL identities before opening ANY camera.
    try:
        identities = resolve_all(config, resolver)
    except (OSError, ConfigError):
        report.update(status="NOT_RUN", reason="missing, invalid or duplicate camera identity")
        return report, 2
    for role in ROLES:
        report["cameras"][role] = runner(config["cameras"][role], identities[role], timeout)
    ok = all(item["status"] == "PASS" for item in report["cameras"].values())
    report.update(status="PASS" if ok else "FAIL", hardware_verified=ok)
    return report, 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true", help="default; no device access")
    modes.add_argument("--capture", action="store_true", help="explicit camera-only one-frame check")
    parser.add_argument("--timeout-seconds", type=float, default=10, help="per-camera bound, 0 < value <= 30")
    parser.add_argument("--output", type=Path, help="new local JSON path; existing files are never overwritten")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 30:
        parser.error("timeout must be finite and in (0, 30]")
    # Reserve output BEFORE any possible camera access. O_EXCL also refuses symlinks.
    stream = None
    if args.output:
        try:
            stream = args.output.open("x", encoding="utf-8")
        except OSError:
            print("Cannot create output; choose a new file in an existing directory.", file=sys.stderr)
            return 2
    try:
        try:
            config = load_config(args.config)
            report, code = check(config, args.capture, args.timeout_seconds)
            report["config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
        except (OSError, ValueError, yaml.YAMLError) as exc:
            report, code = {"status": "FAIL", "schema_valid": False, "error_type": type(exc).__name__}, 2
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        json.dump(report, stream or sys.stdout, ensure_ascii=False, indent=2, allow_nan=False)
        (stream or sys.stdout).write("\n")
        return code
    finally:
        if stream:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
