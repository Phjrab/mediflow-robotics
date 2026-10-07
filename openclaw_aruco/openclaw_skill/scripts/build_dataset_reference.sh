#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: build_dataset_reference.sh CONFIG_JSON REPOSITORY_ROOT OUTPUT_JSON" >&2
  exit 64
fi

config_path="$1"
repository_root="$2"
output_path="$3"

if [[ ! -f "$config_path" ]]; then
  echo "dataset config does not exist: $config_path" >&2
  exit 66
fi
if [[ ! -d "$repository_root" ]]; then
  echo "repository root does not exist: $repository_root" >&2
  exit 66
fi

cd "$repository_root"
python3 -m openclaw_aruco.build_dataset_reference \
  --config "$config_path" \
  --output "$output_path"
