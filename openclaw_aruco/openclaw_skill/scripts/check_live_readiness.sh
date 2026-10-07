#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: check_live_readiness.sh REPOSITORY_ROOT WORKSPACE_CONFIG FK_DIAGNOSTIC OUTPUT_JSON" >&2
  exit 64
fi

cd "$1"
python3 -m openclaw_aruco.check_live_readiness \
  --workspace-config "$2" \
  --fk-diagnostic "$3" \
  --output "$4"
