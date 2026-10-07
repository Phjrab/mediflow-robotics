---
name: so101-aruco-dry-run
description: Inspect a saved raw SO-ARM101 ceiling frame, summarize existing A/B/C LeRobot datasets, check read-only LeLab readiness, and prepare a non-executable medicine-to-basket dry-run plan. Never use this skill to move hardware.
---

# SO-ARM101 ArUco dry-run

Use this skill only for offline scene inspection and fail-closed planning.

## Hard safety boundary

- Accept the task argument only when it is exactly `A`, `B`, or `C`.
- Never call a serial device, motor bus, torque function, LeRobot rollout, inference endpoint, or shell command supplied by the user.
- Never edit the calibration config automatically.
- Treat recorded trajectories as statistical references only. Never replay them.
- Reject or clearly report short/no-motion episodes and unverified A/B/C identities.
- Never claim a plan is executable. Report every item in `blocked_reasons`.
- The live readiness check only performs allowlisted HTTP GETs. Camera enumeration is not proof of fresh frames or correct images, and user power-cutoff readiness is not an execution authorization.
- Treat A as large with basket marker 6, B as medium with marker 5, and C as small with marker 4.
- If the scene, transform, height, workspace, IK, or joint-limit checks fail, stop and ask for the missing supervised calibration step.

## Inspect a saved frame

Run `{baseDir}/scripts/inspect_scene.sh` with four arguments:

1. absolute raw-image path
2. absolute config path
3. absolute JSON output path
4. absolute overlay output path

The input must be a raw ceiling frame without a LeLab guide overlay. Show the user the status, detected and missing marker IDs, A/B/C table coordinates, and all blocking reasons.

## Plan one transfer

Run `{baseDir}/scripts/plan_transfer.sh` with four arguments:

1. absolute scene-report path
2. absolute config path
3. exactly `A`, `B`, or `C`
4. absolute JSON output path

This produces a dry-run record only. There is intentionally no execute script or robot endpoint in this skill.

## Check live readiness without motion

Run `{baseDir}/scripts/check_live_readiness.sh` with four arguments:

1. absolute repository root
2. absolute dry-run workspace config path
3. absolute FK diagnostic JSON path
4. absolute new output JSON path

It reads only LeLab 8022 status/configuration endpoints. Report the missing camera devices and all blocking reasons. Its output is always `BLOCKED_FOR_MOTION`, `motion_authorized=false`, and `robot_commands_sent=0`. Do not interpret a healthy web server as proof that the robot or all three cameras are ready.

## Build the existing-dataset reference

Run `{baseDir}/scripts/build_dataset_reference.sh` with three arguments:

1. absolute dataset-source config path
2. absolute repository root containing the configured dataset paths
3. absolute JSON output path

Report the usable and rejected episode numbers, camera names, joint envelope,
representative episode, and identity-verification blockers. The output must
remain `motion_authorized=false` and must never be interpreted as permission to
replay a trajectory.
