#!/usr/bin/env bash
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
python_bin="/home/USER/miniconda3/envs/lerobot-3060/bin/python"
repo_id="Supermassive111/medicine_c_to_basket_c_3cam_test_20260928_154245"
dataset_root="${project_root}/work/lerobot_datasets/${repo_id}"
output_dir="${1:-${project_root}/work/lerobot_outputs/act_c_3cam_10000_20260928_163650}"

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
  --job_name=act_c_3cam_10000 \
  --steps=10000 \
  --batch_size=8 \
  --num_workers=2 \
  --prefetch_factor=2 \
  --persistent_workers=true \
  --save_checkpoint=true \
  --save_freq=1000 \
  --log_freq=50 \
  --wandb.enable=false \
  --eval_steps=0
