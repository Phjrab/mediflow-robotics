#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: inspect_scene.sh RAW_IMAGE CONFIG OUTPUT_JSON OUTPUT_OVERLAY" >&2
  exit 64
fi

python3 -m openclaw_aruco.inspect_scene \
  --image "$1" \
  --config "$2" \
  --json-output "$3" \
  --overlay-output "$4"
