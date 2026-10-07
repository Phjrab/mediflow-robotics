#!/usr/bin/env bash
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
python_bin="/home/USER/miniconda3/envs/lerobot-3060/bin/python"
repo_id="Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v3_trim05_20261006"
dataset_root="${project_root}/work/lerobot_datasets/Supermassive111/Supermassive111_medicine_a_to_basket_a_3cam_v3_trim05_20261006"
output_dir="${project_root}/work/lerobot_outputs/act_a_v3_trim05_3cam_100k_20261006"
job_name="ACT-A-V3__medicine-a-to-basket-a__3cam__trim05__100k"
validation="${project_root}/outputs/offline_act_a_v3_trim05_20261006/dataset_validation.json"

if [[ ! -x "${python_bin}" || ! -f "${dataset_root}/meta/info.json" ]]; then
  echo "Training Python or derived dataset is missing" >&2
  exit 2
fi
if [[ ! -f "${validation}" ]]; then
  echo "Dataset validation report is missing" >&2
  exit 3
fi
if [[ -e "${output_dir}" ]]; then
  echo "Refusing to overwrite existing training output: ${output_dir}" >&2
  exit 4
fi
if ! "${python_bin}" -c 'import torch; assert torch.cuda.is_available(), "CUDA GPU is unavailable"'; then
  echo "CUDA GPU unavailable; not starting 100k-step training" >&2
  exit 5
fi

export HF_HOME="${project_root}/work/hf-lerobot-cache"
export XDG_CACHE_HOME="${project_root}/work/torch-cache"
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export WANDB_ENTITY="gyeyeongjo-chosun-university"
export WANDB_PROJECT="mediflow-so101-act"
export WANDB_MODE="${WANDB_MODE:-online}"

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
  --wandb.notes="A-v3 trim05: original 60 A episodes preserved, 27564 retained frames, three cameras, 100k ACT steps" \
  --eval_steps=0
