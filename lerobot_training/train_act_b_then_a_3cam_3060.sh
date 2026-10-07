#!/usr/bin/env bash
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
runner="${project_root}/lerobot_training/train_act_single_3cam_3060.sh"

b_repo="Supermassive111/Supermassive111_medicine_b_to_basket_b_3cam_v1_20260928_203749"
b_root="${project_root}/work/lerobot_datasets/Supermassive111/Supermassive111_medicine_b_to_basket_b_3cam_v1_20260928_203749"
b_output="${1:-${project_root}/work/lerobot_outputs/act_b_3cam_10000_20260928_214354}"

a_repo="Supermassive111/medicine_a_to_basket_a_3cam_v1_20260928_211312"
a_root="${project_root}/work/lerobot_datasets/Supermassive111/medicine_a_to_basket_a_3cam_v1_20260928_211312"
a_output="${2:-${project_root}/work/lerobot_outputs/act_a_3cam_10000_20260928_214354}"

echo "[B] starting: ${b_repo}"
"${runner}" "${b_repo}" "${b_root}" "${b_output}" "ACT-B__medicine-b-to-basket-b__3cam__10k"
echo "[B] complete: ${b_output}"

echo "[A] starting: ${a_repo}"
"${runner}" "${a_repo}" "${a_root}" "${a_output}" "ACT-A__medicine-a-to-basket-a__3cam__10k"
echo "[A] complete: ${a_output}"
