#!/usr/bin/env bash
set -euo pipefail
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname "$here")
py="$HOME/venvs/smolvla-py310/bin/python"
export SO101_CONFIG="$here/config.yaml"
export PYTHONPATH="$here:$here/deps:$root/sim:$root/analysis:$root/tools"
export LD_LIBRARY_PATH="$HOME/smolvla-cusparselt-W9m8A2/libcusparse_lt-linux-aarch64-0.6.3.2-archive/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mode=--dry-run
episode_replay=0
for argument in "$@"; do
    case "$argument" in --live|--dry-run|--mock|--simulate|--policy-check|--offline-eval|--camera-check|--recorded-replay) mode=$argument;; esac
    case "$argument" in --replay|--replay=*) episode_replay=1;; esac
done
if [[ ( $mode == --live || $mode == --recorded-replay ) && ${SO101_IN_TMUX-} != 1 ]]; then
    command -v tmux >/dev/null || { echo 'tmux 미설치'; exit 2; }
    launch=$(mktemp -d "$root/runs/LAUNCH_XXXXXXXX")
    printf -v command 'env SO101_IN_TMUX=1 SO101_RUN_RECEIPT=%q bash %q ' "$launch" "$here/run.sh"
    printf -v args '%q ' "$@"
    code=0
    tmux new-session -A -s so101-vla -c "$root" "$command$args" || code=$?
    "$py" -B "$here/run_exit_report.py" "$launch"
    if [[ -s $launch/exit_code ]]; then
        read -r code < "$launch/exit_code"
    fi
    exit "$code"
fi
if [[ $mode == --mock || $mode == --simulate || $mode == --policy-check || $mode == --offline-eval ]]; then
    exec "$py" -B "$here/entry.py" "$@"
fi
stage='실행 잠금 확인'
restore_services=0
finish() {
    local code=$?
    trap - EXIT
    if (( restore_services )); then
        restore || code=1
    fi
    if [[ -n ${SO101_RUN_RECEIPT-} ]]; then
        printf 'EXIT %s (코드 %d)\n' "$stage" "$code" > "$SO101_RUN_RECEIPT/end_stage"
        printf '%d\n' "$code" > "$SO101_RUN_RECEIPT/exit_code"
    fi
    exit "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
exec {lock_fd}>"${XDG_RUNTIME_DIR:-/tmp}/so101-vla-live.lock"
flock -n "$lock_fd" || { echo '다른 VLA 실행 중'; exit 2; }
stage='sudo 인증'
sudo -v
stage='서비스 상태 확인'
services=(lelab-jetson-py310.service medicine-yolo-preview.service so101-camera-preview.service astra-v4l2-bridge.service)
alignment_original=$(systemctl --user is-active so101-camera-alignment.service || true)
original=()
for s in "${services[@]}"; do
    if [[ $s == lelab-* ]]; then original+=("$(systemctl --user is-active "$s" || true)")
    else original+=("$(systemctl is-active "$s" || true)"); fi
done
restore() {
    local restore_code=0
    for i in 1 2; do
        s=${services[$i]}
        [[ ${original[$i]} != active ]] || sudo -n systemctl start "$s" || restore_code=1
    done
    if [[ ${original[3]} != active ]]; then sudo -n systemctl stop astra-v4l2-bridge.service || restore_code=1; fi
    [[ ${original[0]} != active ]] || systemctl --user start lelab-jetson-py310.service || restore_code=1
    if [[ $alignment_original == active || $alignment_original == activating ]]; then
        systemctl --user start so101-camera-alignment.service || restore_code=1
    fi
    return "$restore_code"
}
restore_services=1
stage='정렬 미리보기 정지'
if [[ $alignment_original == active || $alignment_original == activating ]]; then
    systemctl --user stop so101-camera-alignment.service
fi
stage='LeLab 사전 확인'
if [[ $mode == --recorded-replay || ( $mode == --live && $episode_replay == 1 ) ]]; then
    "$py" -B "$root/tools/replay_preflight.py"
else
    "$py" -B "$root/tools/stop_lelab_activity.py"
fi
stage='LeLab 및 카메라 서비스 전환'
systemctl --user stop lelab-jetson-py310.service
sudo -n systemctl stop medicine-yolo-preview.service so101-camera-preview.service
sudo -n systemctl start astra-v4l2-bridge.service
stage='실행기 시작'
"$py" -B "$here/entry.py" "$@"
