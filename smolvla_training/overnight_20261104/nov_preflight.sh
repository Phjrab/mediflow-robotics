#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$task_root"
require_no_owners() {
  local result="$1" detail="$2"
  if test "$result" -ne 1 || test -n "$detail"; then
    echo 'PREFLIGHT_BLOCKED: 포트 점유 또는 점유 조회 오류. 자동 해제하지 않음'
    return 1
  fi
}
if systemctl --user is-active --quiet lelab-jetson-py310.service; then
  echo 'PREFLIGHT_BLOCKED: LeLab active'; exit 1
fi
port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00
test -e "$port" || { echo 'PREFLIGHT_BLOCKED: follower 경로 없음'; exit 1; }
set +e
detail=$(sudo -n fuser -v "$port" 2>&1)
result=$?
set -e
require_no_owners "$result" "$detail"
echo 'PORT_FREE: follower 비점유 확인'
if test -e "$task_root/SERIAL_STOP.json"; then
  echo 'PREFLIGHT_BLOCKED: serial 전역 중단 기록 존재. 토크ON/45도 조건을 우회하지 않음'; exit 2
fi
systemctl is-active --quiet astra-v4l2-bridge.service || { echo 'PREFLIGHT_BLOCKED: Astra 브리지 inactive'; exit 1; }
task_camera_paths=$("$HOME/venvs/smolvla-py310/bin/python" -B -c 'from observation_contract import load_contract; print("\n".join(load_contract()["cameras"].values()))')
while IFS= read -r camera; do
  test -e "$camera" || { echo 'PREFLIGHT_BLOCKED: 카메라 경로 없음'; exit 1; }
done <<< "$task_camera_paths"
echo 'PREFLIGHT_PASS: 비점유·서비스·경로·중단기록 검사 통과'
