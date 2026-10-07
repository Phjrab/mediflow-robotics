# OpenClaw + ArUco dry-run bridge

This directory contains the first, read-only stage of the SO-ARM101 OpenClaw bridge.
It detects ArUco markers and three visible bottle caps in a saved **raw** ceiling
frame. It projects live cap centers and basket marker positions into the table
coordinate frame for inspection only.

The saved-frame inspector does not open a camera. The separate live readiness
check asks LeLab to enumerate cameras, which briefly probes devices but does
not start recording or move the robot. Neither tool imports LeRobot, connects
to serial devices, enables torque, or calculates authorized robot targets.

## Current A/B/C mapping

- A: large bottle, basket marker ID 6
- B: medium bottle, basket marker ID 5
- C: small bottle, basket marker ID 4

Bottle identity currently follows the user-confirmed fixed visible C/B/A
left-to-right placement. X marks are ignored.

## Run the saved-frame inspection

```bash
python3 -m openclaw_aruco.inspect_scene \
  --image outputs/openclaw_aruco/openclaw_raw_vertical.jpg \
  --config openclaw_aruco/config/dry_run_workspace.json \
  --json-output outputs/openclaw_aruco/scene_report_20261005.json \
  --overlay-output outputs/openclaw_aruco/scene_overlay_20261005.jpg
```

The command refuses to overwrite outputs. A report may be
`READY_FOR_DRY_RUN` while physical motion remains blocked. The report keeps
`robot_enabled=false` and lists the missing robot transform, workspace limits,
and vertical heights.

## Known limitation

The existing YOLO model returned zero detections on the 2026-10-05 live raw
frame. The dry-run prototype therefore finds the three visible cap circles and
assigns them by the fixed left-to-right order C/B/A. This is not general bottle
classification. Because a cap is above the table plane, its RGB projection is
not yet a validated robot grasp coordinate; depth and TCP calibration remain
mandatory.

## Generate a blocked dry-run plan

The current configuration intentionally cannot produce executable robot
targets. It is missing the measured world-to-robot transform, XYZ workspace
limits, vertical heights, and a validated IK solver/joint limits. The following
command records those blockers without opening a camera or robot:

```bash
python3 -m openclaw_aruco.plan_transfer \
  --scene outputs/openclaw_aruco/scene_report_20261005.json \
  --config openclaw_aruco/config/dry_run_workspace.json \
  --task A \
  --json-output outputs/openclaw_aruco/plan_a_20261005.json
```

`openclaw_skill/` is a draft OpenClaw workspace skill. It exposes only saved
frame inspection, read-only LeLab readiness checks, and dry-run planning. There
is deliberately no execution script or robot endpoint. The draft skill is not
installed in a running OpenClaw Gateway.

## Read-only live readiness check

```bash
python3 -m openclaw_aruco.check_live_readiness \
  --workspace-config openclaw_aruco/config/dry_run_workspace.json \
  --fk-diagnostic outputs/openclaw_aruco/plan_a_episode10_fk_diagnostic_20261005.json \
  --output outputs/openclaw_aruco/live_readiness_20261006.json
```

It only calls allowlisted LeLab 8022 GET endpoints. It compares the three
configured camera indices against the enumerated devices and preserves all
physical-calibration blockers. A preview can hold a camera open and temporarily
keep it out of enumeration; this is reported as unverified, not definitely
broken. Enumeration does not validate live frames.

## Audit saved actions against current follower calibration

The current follower calibration does not by itself certify mechanical or
collision limits. For an offline, read-only comparison of the existing A/B/C
`action` values against those calibrated ranges, run:

```bash
work/venvs/mediflow-vlm/bin/python -m openclaw_aruco.reference.audit_dataset_actions \
  --dataset-config openclaw_aruco/config/dataset_sources.json \
  --ranges openclaw_aruco/reference/follower_calibrated_ranges_20261006.json \
  --output outputs/openclaw_aruco/dataset_action_follower_range_audit_20261006.json
```

The output already exists from the 2026-10-06 audit; choose a new output path
to rerun. Recorded A/B/C actions exceed today's follower calibration at Elbow
and Wrist Flex in some episodes. Do not replay them directly. See
`docs/OPENCLAW_OFFLINE_TCP_REVIEW_20261006.md` for the counts and caveats.

## Build a reference library from the existing A/B/C datasets

The recorded 60-episode datasets can be used without recording them again.
This command extracts joint envelopes, a representative episode, and a
normalized median/q10/q90 trajectory for each task:

```bash
python3 -m openclaw_aruco.build_dataset_reference \
  --config openclaw_aruco/config/dataset_sources.json \
  --output outputs/openclaw_aruco/dataset_reference_abc_20261005.json
```

The result is always reference-only. It cannot enable torque or replay an
episode. The user confirmed on 2026-10-05 that the recorded identities are
A=large, B=medium, and C=small, so the identity check is recorded while motion
authorization remains disabled.

## Nominal gripper-frame analysis (not physical calibration)

TheRobotStudio's SO-101 URDF contains a nominal `gripper_frame_joint` absent
from the local reference URDF. `reference/nominal_gripper_frame.json` records
that CAD transform for offline analysis only. `reference/nominal_tcp_diagnostic.py`
evaluates a motion-disabled recorded path in that frame and reports local URDF
limit violations; it cannot authorize a path or move hardware. See
`docs/OPENCLAW_OFFLINE_TCP_REVIEW_20261006.md` for findings and caveats.
