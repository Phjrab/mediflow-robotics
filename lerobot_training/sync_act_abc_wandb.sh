#!/usr/bin/env bash
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
wandb_bin="/home/USER/miniconda3/envs/lerobot-3060/bin/wandb"
found=0

export WANDB_CACHE_DIR="${project_root}/work/wandb-cache"
mkdir -p "${WANDB_CACHE_DIR}"

for run_dir in \
  "${project_root}"/work/lerobot_outputs/act_c_3cam_10000_20260928_163839/wandb/offline-run-* \
  "${project_root}"/work/lerobot_outputs/act_b_3cam_10000_20260928_214354/wandb/offline-run-* \
  "${project_root}"/work/lerobot_outputs/act_a_3cam_10000_20260928_214354/wandb/offline-run-*; do
  [[ -d "${run_dir}" ]] || continue
  found=1
  "${wandb_bin}" sync "${run_dir}"
done

if [[ "${found}" -eq 0 ]]; then
  echo "No ACT A/B/C W&B offline runs found." >&2
  exit 1
fi
