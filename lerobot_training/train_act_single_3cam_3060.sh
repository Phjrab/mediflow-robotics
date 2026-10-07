#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 <repo_id> <dataset_root> <output_dir> <job_name>" >&2
  exit 2
fi

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
python_bin="/home/USER/miniconda3/envs/lerobot-3060/bin/python"
repo_id="$1"
dataset_root="$2"
output_dir="$3"
job_name="$4"

if [[ ! -f "${dataset_root}/meta/info.json" ]]; then
  echo "dataset metadata not found: ${dataset_root}/meta/info.json" >&2
  exit 3
fi

if [[ -e "${output_dir}" ]]; then
  echo "refusing to overwrite existing output: ${output_dir}" >&2
  exit 4
fi

export HF_HOME="${project_root}/work/hf-lerobot-cache"
export XDG_CACHE_HOME="${project_root}/work/torch-cache"
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"

exec "${python_bin}" -m lerobot.scripts.lerobot_train \
  --dataset.repo_id="${repo_id}" \
  --dataset.root="${dataset_root}" \
  --dataset.video_backend=pyav \
  --policy.type=act \
  --policy.device=cuda \
  --policy.use_amp=false \
  --policy.push_to_hub=false \
  --output_dir="${output_dir}" \
  --job_name="${job_name}" \
  --steps=10000 \
  --batch_size=8 \
  --num_workers=2 \
  --prefetch_factor=2 \
  --persistent_workers=true \
  --save_checkpoint=true \
  --save_freq=1000 \
  --log_freq=50 \
  --wandb.enable=true \
  --wandb.project=mediflow-so101-act \
  --wandb.mode=offline \
  --wandb.disable_artifact=true \
  --wandb.notes="SO-ARM101 medicine sorting, 60 episodes, three cameras, ACT 10k steps" \
  --eval_steps=0
