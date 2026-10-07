#!/usr/bin/env python3
"""Inspect one raw ceiling image without opening a robot or camera device."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from openclaw_aruco.scene import inspect_scene


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--overlay-output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for output in (args.json_output, args.overlay_output):
        if output.exists():
            raise ValueError(f"output already exists: {output}")
        if not output.parent.is_dir():
            raise ValueError(f"output parent does not exist: {output.parent}")
    report, overlay = inspect_scene(args.image, args.config)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not cv2.imwrite(str(args.overlay_output), overlay):
        raise OSError(f"failed to write overlay: {args.overlay_output}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
