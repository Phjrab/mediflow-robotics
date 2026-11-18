#!/usr/bin/env bash
set -euo pipefail
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec bash "$root/live/run.sh" --recorded-replay "$root/reports/20261115/GRASP_CAUSE_7_EPISODES/replay_blue.json" --speed 0.33 "$@"
