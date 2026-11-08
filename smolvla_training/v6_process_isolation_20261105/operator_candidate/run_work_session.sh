#!/usr/bin/env bash
set -euo pipefail
taskRoot=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec bash "$taskRoot/run_first_from_hold.sh" --work-session "$@"
