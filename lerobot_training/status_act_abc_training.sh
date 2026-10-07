#!/usr/bin/env bash
set -euo pipefail

unit="mediflow-act-b-then-a-10000-20260928.service"
project_root="/home/USER/Documents/Codex/2026-09-22/so101-pilot-dataset-ready-tar-gz-2"

systemctl --user show "${unit}" \
  -p ActiveState -p SubState -p MainPID -p Result 2>/dev/null || true

journalctl --user -u "${unit}" -o cat --no-pager 2>/dev/null \
  | tr '\r' '\n' \
  | grep 'ot_train.py:641' \
  | tail -n 5 || true

for label in c b a; do
  latest="$(find "${project_root}/work/lerobot_outputs" -maxdepth 1 -type d \
    -name "act_${label}_3cam_10000_*" -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
  [[ -n "${latest}" ]] || continue
  checkpoint="$(find "${project_root}/work/lerobot_outputs/${latest}/checkpoints" -mindepth 1 -maxdepth 1 \
    -type d -printf '%f\n' 2>/dev/null | sort | tail -n 1 || true)"
  echo "${label^^}: ${latest} latest_checkpoint=${checkpoint:-none}"
done

nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || true
