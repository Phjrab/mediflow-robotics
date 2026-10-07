# ACT-A V3 body-tracking action progression

Date: 2026-10-07 KST. Implemented at the operator's request following trial `1791365427`, where gripper reopening advanced while elbow was about 21.1 degrees behind its simultaneous model target.

Latest physical trial `1791368943` progressed toward A, then timed out after about11.25 seconds with elbow error4.014898 degrees against the4-degree tolerance. The operator reported an open-gripper arm drop onto the desk after stopping, without apparent damage. Source inspection confirmed the old cleanup disabled motor torque. The software fix below is deployed but has **not** been physically tested. See `trial_1791368943/RESULT.md`.

## Implemented behavior

Only the existing Jetson model folder `act-a-v3-trim05-3cam-100k` has a new `lelab_tracking_gate.json`. LeLab passes its validated configuration to the rollout child as `LEROBOT_ROLLOUT_TRACKING_GATE_JSON`. The launcher rejects this option for non-ACT/relative-action policies; the base strategy restricts it to the synchronous engine.

- Before advancing the policy or the next interpolation sample, compare observed **five body-joint positions** with the previous processed robot target.
- If any body error exceeds its tolerance plus0.1-degree measurement margin (effective elbow4.1 degrees, the other four joints3.1), send the previous full action again through the existing action processor and the robot's existing5-degree clamp. Do not consume another policy or interpolation action. Thus a later release command remains pending while the prior body target is incomplete. The bounded0.1-degree margin addresses fractional targets versus encoder quantization (nominal360/4096 =0.0879 degrees/tick); it does not explain the underlying4-degree steady error or justify further blind tolerance increases.
- Gripper feedback does not participate in the body-reached test: object contact normally prevents an empty-jaw closing target from being reached. Holding the complete previous action preserves its gripper target during the pause.
- A body target that remains incomplete for 2 seconds raises `TrackingGateError`, stops the sequence, and suppresses automatic return-to-start. Missing/non-finite body feedback and non-finite actions are rejected. A stop received during an observation read is honored before another gated action is sent.
- For this opted-in mode's fault, explicit stop, or unresolved final target, cleanup now reads fresh `Present_Position` for all motors, sends those positions through the existing robot wrapper/clamp, and temporarily sets `disable_torque_on_disconnect=False` while closing serial/cameras. The configuration flag is restored afterward; normal nontracking cleanup is unchanged. If the fresh read/hold command fails, no guessed recovery position is sent: existing goals and torque are retained with an operator-recovery warning. This fallback cannot guarantee immobility. This change does **not** enable torque that is already off, provide a mechanical brake, prevent power-loss falls, prove collision safety, or guarantee continued bottle grasp. Motors remain energized after such a stop: operator supervision/recovery is required, and prolonged stalled holding can heat motors. Do not force joints by hand. A normally completed run without a fault still uses the existing return/disconnect behavior.
- Joint traces now include `tracking_gate.held`, previous body errors, and target age. Camera-frame capture remains enabled. A tracking gate is a sequence synchronizer, not a basket/clearance detector, grasp verifier, or collision-safe workspace.

Maximum requested duration remains 60 seconds. The model checkpoint, camera mapping, 5-degree relative-target clamp, teleoperation configuration, and 15-Hz policy/2x interpolation settings were not changed. A paused sequence can take longer; reaching the duration limit does not mean it completed the task. The first action still goes through the original clamp; this change gates subsequent sequence advancement and cannot correct a policy that predicts the wrong spatial goal.

## Verification

Local and Jetson Python both passed13 no-robot tests: the previous9 tests plus quantization-margin boundary handling, present-position hold/torque-preserving disconnect, read-loss fallback without guessed positions, and unchanged ordinary cleanup. Tests used real interpolation and fake delayed/stalled robot feedback, with no physical robot or camera handles opened. These verify code behavior, not physical stopping/holding performance.

Installed modules compiled/imported on Jetson. A separate mocked LeLab launch verified that a 60-second request receives the 3-degree/2-second tracking configuration, retains clamp5 and joint trace, and starts no real child process. The mock wrote only to a temporary directory, not real inference history.

All jobs were inactive before deployment/restart. 8022 health is OK; inference, teleop, and recording remain inactive. Both3-degree and elbow4-degree tracking modes ran physically and timed out; neither completed placement. No further motion was initiated after the reported desk contact. The torque-preserving stop and0.1-degree margin are software-tested only. Next, inspect gripper/joints for damage or binding and verify small supervised teleop movements before considering another policy trial. The prior model-specific start-pose bypass remains active and is not validated by these software tests.

## Deployment and rollback

Installed root: `/home/USER/venvs/lelab-jetson-py310/lib/python3.10/site-packages/`.

| Module | Deployed SHA-256 |
| --- | --- |
| `lerobot/rollout/strategies/base.py` | `e471ab57f5642265b9db4dbb6ce0cef442de1006315ecbfc4f453eb68b4372f0` |
| `lerobot/rollout/strategies/core.py` | `50ed8d9209631f634d6c6627c9abd9869a21d793e11c276d5a745f4ba3f6c557` |
| `lerobot/rollout/strategies/tracking_gate.py` | `e74511da7499b71cf334b7a3b4bba62e37c2ac67c5502e51cedf2896f044d0fd` |
| `lelab/rollout.py` | `282f2f50e15c369c6a250d650b104a2dcb1e0717ccd7a682322ebe3dc02319a2` |

The initial existing-module backups retain suffix `.20261007_before_tracking_gate.bak`. Before the latest deployment, base/core/tracking and the elbow4 model config were additionally preserved beside the installed files with suffix `.20261007_before_torque_preserving_stop.bak`. Local and installed hashes matched. Current model configuration SHA-256 is `dca6b7e4a5da7d3d8236d10893fbc583d46ba53092f075c91fa096384580b763`. Previous elbow4 config hash was `bde8b1a4475e0940cce3c6fea15e8927344c03a22bccd453dfebc968ae6d74cf`; original all3 config (`0b0667b590ccbdc34ebd44ee011b4c370cb926149b3918ba968c9795d2b4e52a`) remains preserved as `lelab_tracking_gate.json.20261007_before_elbow4_tracking.bak`.

To disable this mode for a subsequent launch without deleting evidence, rename the model folder's `lelab_tracking_gate.json` to a `.disabled` name while no rollout is active. The launcher only reads the exact `.json` name. This also disables its torque-preserving fault cleanup and returns to ordinary disconnect behavior; do not use that as a recovery action during a fault. Restoring old module backups reinstates the identified torque-off-drop risk on tracking abort. Do not modify files during an active rollout.
