# First body-tracking comparison: early reach-tolerance timeout

2026-10-07 KST. Operator restored the scene/start and requested the comparison. Existing power-cutoff/supervised-test authorization was retained; no repeated authorization question was needed.

## Result

The 8000 service had been restarted and PID 518206 held the two USB cameras. After checking its inference/teleop/recording all inactive, stopped only `lelab.service` again. Oblique/wrist probes yielded 75/77 frames in 3 s; Astra sequence advanced. Released previews, then requested one corrected ACT-A V3 trim05 inference with maximum duration 60 s, original clamp 5 and 3-degree body reach tolerances/2-second target timeout.

The model loop started around 19:18:33 and stopped around 19:18:35 with `TrackingGateError`, exit 1. It saved 61 samples (last elapsed 2.704572 s) and 4 camera frames. The approximately 0.727-second first inference preceded the target-wait clock, so total movement-loop time exceeds the two-second target timeout. Standard disconnect completed at 19:18:36; automatic home return was correctly skipped.

Operator observed a small initial lift then stop. The 2-second oblique frame shows bottles/baskets still in position with no completed approach/grasp. The policy target remained the first action through the trace; later release actions were not consumed. This verifies the hold/timeout mechanism on hardware, but is not a successful approach or placement.

## Why it stopped

Initial elbow was 96.484 degrees. First model target was 84.329 degrees; the initial clamped command was 91.484. Later the actual sent command reached 84.329, but observed elbow settled at 87.780, retaining **3.451 degrees** error through the final samples. Other body-joint errors at timeout were <=0.189 degrees. Because the elbow stayed slightly beyond the configured 3-degree reach tolerance, progression never resumed and the two-second timeout fired.

This is a reach tolerance too tight for the observed steady residual, not a camera/startup failure. The underlying reason for the residual (servo response, friction/load, or another controller effect) has not been measured. No motor-gain/torque/command-limit change was made.

## Refined setting prepared for next comparison

Changed only elbow reach tolerance 3 -> 4 degrees. Other four body tolerances remain 3, timeout 2 s, motor relative-target clamp 5, runtime maximum 60 s. New config hash: `bde8b1a4475e0940cce3c6fea15e8927344c03a22bccd453dfebc968ae6d74cf`. Remote original is backed up as `lelab_tracking_gate.json.20261007_before_elbow4_tracking.bak` (original `0b0667b590ccbdc34ebd44ee011b4c370cb926149b3918ba968c9795d2b4e52a`). All jobs were inactive for configuration update; the launcher reads the file on each launch, so no restart was necessary.

Nine no-robot tests passed locally and on Jetson. The added synthetic steady-offset test demonstrates timeout at 3 degrees, progress at 4 for a 3.45-degree residual, and continued holding for a 21-degree body lag. This does not guarantee later targets are reachable or task placement will succeed.

The new elbow 4-degree setting has **not** run physically yet. Asked the operator to restore only the arm using teleoperation (bottles/baskets appear unchanged), then report readiness. Do not automatically retry from the timeout pose.
