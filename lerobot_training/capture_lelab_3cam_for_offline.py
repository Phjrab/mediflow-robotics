#!/usr/bin/env python3
"""Capture fresh LeLab camera images for offline-only policy checks."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image


def digest(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()


def decode_check(image: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image)) as decoded:
        decoded.verify()
    with Image.open(io.BytesIO(image)) as decoded:
        return decoded.size


def mjpeg_frames(url: str, count: int = 2) -> list[bytes]:
    frames: list[bytes] = []
    buffer = b""
    with urlopen(url, timeout=8) as response:
        while len(frames) < count:
            block = response.read(16384)
            if not block:
                raise RuntimeError(f"camera stream ended before {count} frames: {url}")
            buffer += block
            while len(frames) < count:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    buffer = buffer[-2:]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    buffer = buffer[start:]
                    break
                frame = buffer[start : end + 2]
                decode_check(frame)
                frames.append(frame)
                buffer = buffer[end + 2 :]
            if len(buffer) > 6 * 1024 * 1024:
                raise RuntimeError(f"oversize camera stream: {url}")
    return frames


def astra_frames(base: str) -> tuple[list[bytes], list[int]]:
    frames: list[bytes] = []
    sequences: list[int] = []
    for _ in range(2):
        with urlopen(f"{base}/camera-snapshot/8?width=640&height=480&fps=30", timeout=8) as response:
            sequences.append(int(response.headers["X-Frame-Sequence"]))
            frames.append(response.read(6 * 1024 * 1024 + 1))
        decode_check(frames[-1])
        time.sleep(0.15)
    return frames, sequences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8022")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing snapshot folder: {output}")
    base = args.base_url.rstrip("/")
    captured: dict[str, dict] = {}
    frames, sequences = astra_frames(base)
    if sequences[1] <= sequences[0] or digest(frames[0]) == digest(frames[1]):
        raise RuntimeError("ceiling_vertical did not advance")
    captured["ceiling_vertical"] = {
        "frames": frames,
        "sequence": sequences,
        "size": decode_check(frames[-1]),
    }
    for name, index in (("ceiling_oblique", 4), ("end_effector", 6)):
        frames = mjpeg_frames(f"{base}/camera-preview/{index}?width=640&height=480&fps=30")
        if digest(frames[0]) == digest(frames[1]):
            raise RuntimeError(f"{name} did not advance")
        captured[name] = {"frames": frames, "size": decode_check(frames[-1])}
    output.mkdir(parents=True)
    audit = {"source": base, "captured_at_unix": time.time(), "cameras": {}}
    for name, record in captured.items():
        (output / f"{name}.jpg").write_bytes(record["frames"][-1])
        audit["cameras"][name] = {
            "size": record["size"],
            "sha256": [digest(frame) for frame in record["frames"]],
            "fresh": True,
            **({"sequence": record["sequence"]} if "sequence" in record else {}),
        }
    (output / "capture_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
