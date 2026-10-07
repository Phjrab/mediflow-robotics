# LeLab 3-camera setup (2026-09-27)

LeLab robot record `so-101` now has three OpenCV cameras:

| LeLab name | Physical role | Camera index | Format | Backend |
|---|---|---:|---|---|
| `ceiling_vertical` | Astra ceiling camera, vertical view | 8 | YUYV, 640x480, 30 fps | V4L2 loopback from OpenNI |
| `ceiling_oblique` | Oblique workspace camera | 4 | MJPG, 640x480, 30 fps | V4L2 |
| `end_effector` | Wrist/end-effector camera | 6 | MJPG, 640x480, 30 fps | V4L2 |

USB topology inspection showed that `/dev/video0` through `/dev/video5` are
all nodes from the same Intel RealSense D435. The former index 2 entry was
therefore not the Astra; it was another RealSense stream. The Orbbec Astra is
an OpenNI-only USB device, so `astra-v4l2-bridge.service` now exposes its RGB
stream as the persistent virtual camera `/dev/video8`.

All three views were visually checked in LeLab after the correction: index 8
is the Astra vertical ceiling view, index 4 is the RealSense oblique workspace
view, and index 6 is the close end-effector view.

## Current service state

- LeLab: active (`systemctl --user restart lelab.service`)
- Astra OpenNI-to-V4L2 bridge: active and enabled
  (`astra-v4l2-bridge.service`, `/dev/video8`)
- overhead preview server on port 8010: stopped and disabled because it
  conflicts with the Astra bridge and the RealSense camera
  (`so101-camera-preview.service`)
- VLM capture server on port 8011: stopped through `/api/shutdown`

The 8010/8011 services must not hold these devices while LeLab records. To switch
back to the VLM capture workflow later:

```bash
sudo systemctl start so101-camera-preview.service
cd /home/USER/so101_vlm_capture
/home/USER/miniconda3/bin/python app.py \
  --host 0.0.0.0 --port 8011 \
  --camera-config config/cameras.json
```

Stop/release the LeLab camera previews before switching back.

No calibration file, leader/follower serial port, dataset, or robot motion was
changed during this setup.
