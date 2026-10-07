#!/usr/bin/env bash
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
python_bin="/home/USER/miniconda3/envs/lerobot-3060/bin/python"
repo_id="Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846"
dataset_root="${project_root}/work/lerobot_datasets/Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v2_20261002_173846"
output_dir="${project_root}/work/lerobot_outputs/act_a_v2_3cam_100k_20261002"
job_name="ACT-A-V2__medicine-a-to-basket-a__3cam__100k"

if [[ ! -x "${python_bin}" ]]; then
  echo "training Python not found: ${python_bin}" >&2
  exit 2
fi

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
export WANDB_ENTITY="gyeyeongjo-chosun-university"
export WANDB_PROJECT="mediflow-so101-act"
export WANDB_MODE="online"

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
  --steps=100000 \
  --batch_size=8 \
  --num_workers=2 \
  --prefetch_factor=2 \
  --persistent_workers=true \
  --save_checkpoint=true \
  --save_freq=5000 \
  --log_freq=50 \
  --wandb.enable=true \
  --wandb.project="${WANDB_PROJECT}" \
  --wandb.entity="${WANDB_ENTITY}" \
  --wandb.mode="${WANDB_MODE}" \
  --wandb.disable_artifact=true \
  --wandb.notes="SO-ARM101 A-v2: 60 episodes, 32597 frames, ceiling vertical + ceiling oblique + end effector, ACT 100k steps" \
  --eval_steps=0
