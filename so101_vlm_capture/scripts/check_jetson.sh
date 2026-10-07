#!/usr/bin/env bash
set -u

DEVICE="${1:-/dev/video2}"
echo "[1/4] 장치 존재 여부"
if [[ ! -e "$DEVICE" ]]; then
  echo "ERROR: $DEVICE 가 없습니다."
  exit 2
fi
ls -l "$DEVICE"

echo "[2/4] 카메라 점유 프로세스 (종료하지 않음)"
if command -v fuser >/dev/null 2>&1; then
  fuser -v "$DEVICE" 2>&1 || true
elif command -v lsof >/dev/null 2>&1; then
  lsof "$DEVICE" 2>&1 || true
else
  echo "INFO: fuser/lsof가 없어 점유 프로세스를 확인하지 못했습니다."
fi

echo "[3/4] 지원 포맷"
if command -v v4l2-ctl >/dev/null 2>&1; then
  v4l2-ctl --device="$DEVICE" --list-formats-ext || true
else
  echo "INFO: v4l2-ctl이 없습니다. sudo apt install v4l-utils 로 설치할 수 있습니다."
fi

echo "[4/4] 640x480 프레임 한 장 읽기 (파일 저장 없음)"
python3 - "$DEVICE" <<'PY'
import sys
import cv2

device = sys.argv[1]
cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)
if not cap.isOpened():
    print(f"ERROR: {device}를 열 수 없습니다. 다른 프로세스 점유 또는 권한을 확인하세요.")
    raise SystemExit(3)
ok, frame = cap.read()
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
cap.release()
if not ok or frame is None:
    print("ERROR: 카메라는 열렸지만 프레임 읽기에 실패했습니다.")
    raise SystemExit(4)
print(f"OK: frame={frame.shape[1]}x{frame.shape[0]}, negotiated={width}x{height}, fps={fps:.2f}")
if frame.shape[1::-1] != (640, 480):
    print("ERROR: 실제 프레임 해상도가 640x480이 아닙니다.")
    raise SystemExit(5)
PY

