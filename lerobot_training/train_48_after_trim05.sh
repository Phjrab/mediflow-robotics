#!/usr/bin/env bash
# This wrapper waits without using the GPU and fails closed if the first run fails.
set -euo pipefail

project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"
first_unit="act-a-v3-trim05-100k.service"
first_checkpoint="${project_root}/work/lerobot_outputs/act_a_v3_trim05_3cam_100k_20261006/checkpoints/100000/pretrained_model/model.safetensors"
second_script="${project_root}/lerobot_training/train_act_a_v3_48episodes_3cam_100k_3060.sh"

echo "Waiting for ${first_unit} to finish successfully before launching the 48-episode run."
while true; do
  active_state="$(systemctl --user show "${first_unit}" -p ActiveState --value)"
  case "${active_state}" in
    active|activating)
      sleep 60
      ;;
    inactive)
      result="$(systemctl --user show "${first_unit}" -p Result --value)"
      exit_code="$(systemctl --user show "${first_unit}" -p ExecMainStatus --value)"
      if [[ "${result}" != success || "${exit_code}" != 0 || ! -s "${first_checkpoint}" ]]; then
        echo "First training did not produce a successful final checkpoint: result=${result}, exit=${exit_code}" >&2
        exit 2
      fi
      echo "First training complete; starting separate 48-episode 100k-step run."
      exec /bin/bash "${second_script}"
      ;;
    *)
      echo "First training stopped unexpectedly: state=${active_state}" >&2
      exit 3
      ;;
  esac
done
