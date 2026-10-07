#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: plan_transfer.sh SCENE_JSON CONFIG TASK OUTPUT_JSON" >&2
  exit 64
fi

case "$3" in
  A|B|C) ;;
  *) echo "task must be exactly A, B, or C" >&2; exit 64 ;;
esac

python3 -m openclaw_aruco.plan_transfer \
  --scene "$1" \
  --config "$2" \
  --task "$3" \
  --json-output "$4"
