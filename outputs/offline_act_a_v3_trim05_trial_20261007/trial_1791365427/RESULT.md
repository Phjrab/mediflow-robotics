# ACT-A V3 trim05: supervised 60-second placement trial

2026-10-07 KST. Operator confirmed immediate power cutoff, clear path, and A-large/A-basket placement, then closed the other computer's LeLab before requesting the retry.

## Execution and camera conflict

The idle 8000 server kept reopening previews after its client was closed. Its PID 515960 held both `/dev/video4` and `/dev/video6`, preventing 8022 capture. `/camera-preview-stop` initially released them but they were reacquired. After verifying 8000 inference, teleoperation, and recording all inactive, stopped only the user unit `lelab.service`. It remains stopped to prevent camera contention; its configuration/data were not deleted. The separate 8022 unit and Astra bridge remain running.

Preflight then received 96 oblique and 107 wrist MJPEG frames during separate 4-second probes; Astra snapshot sequence advanced. Preview handles were released before rollout.

Started `/home/USER/so101-medicine-bootstrap/models/act-a-v3-trim05-3cam-100k` on follower `/dev/ttyACM0` using existing `so-101.json`, all three configured 640x480/30-FPS cameras, existing 5-degree relative-target clamp, 15-Hz policy schedule, and 2x interpolation. Requested duration was 60 seconds. The policy loop ran from approximately 18:30:58 to 18:31:58, returned toward its initial position, disconnected at 18:32:02, and exited 0. This is process completion, **not placement success**.

Log `1791365427.log`, trace `1791365427.joints.jsonl` (1,730 samples, last time 59.993 s), and two-view image trace `1791365427.joints.frames/` (60 frames) are copied into this directory. Frame copies were successfully saved; processing median was 0.051 ms, maximum 0.487 ms. These timings include processing and the sampled array copies and do not isolate copying overhead. Camera observation median was 1.876 ms. The longest loop was the initial inference at 0.859 s; later inference spikes also appear in the log.

## What happened physically

The operator reported slight tremor, good approach/grasp, then tilting/pushing the bottle instead of putting it into the basket. The two-second image sequence corroborates the placement failure:

- Around 6-8 s, the fingers approach and engage A-large.
- At 10.09 s, the bottle has moved and tilted in the grip near the rear of the basket.
- At 12.11-14.12 s, the arm/bottle intersects the basket edge; the blue A basket and neighboring basket are visibly displaced. At 14.12 s A is outside the basket, lying near the rear/side edge.
- At 16-22 s the arm withdraws and the bottle remains outside. Later frames do not show recovery or successful placement.

The coarse frame interval cannot establish the exact contact or first slip instant. It does show failure during transport/deposit rather than a lack of initial bottle recognition. Do not repeat from this post-trial scene: objects and baskets have moved.

## Why complete training demonstrations did not guarantee success

Successful reference episodes 0 and 15, extracted from the exact trim05 dataset, show the bottle seated inside A's basket before the gripper withdraws. Both examples include placement. The references use different wrist/grasp trajectories; one example is not a universal required pose.

The real loop consumes subsequent policy actions without checking whether the arm reached the previous target (`send_next_action` advances the inference engine/interpolator). The limiter changes the effective body targets while policy/gripper progression continues. At the first sustained reopening transition, 12.610655 s (heuristic target >35 after an approach-open/close sequence):

- Model elbow target: -81.19 degrees.
- Observed elbow before sending: -60.09 degrees, about **21.10 degrees behind the model target**.
- Actual elbow command after clamp: -65.44 degrees.
- Gripper model target: 36.47; actual command: 31.32, from measured 25.34 on the normalized gripper scale.

Thus the open/release sequence advanced while the body had not reached the model's simultaneous intended pose. This is concrete evidence for command/physical-motion desynchronization. It is not proof that this alone caused every spatial error. Model generalization, grip geometry, and scene/calibration differences remain possible contributors.

During the first 20 s, elbow targets were clipped in 393/564 samples and shoulder-lift targets in 246/564. At 18 s the model lift target was about -97.4 while observed lift was -11.7; the actual sent target was about -17.6. This difference is pre-clamp intent versus observed pose, **not an 85-degree error against the actual motor command**. Actual sent targets and positions track far more closely.

Initial gripper was 5.45, unlike the previous failed trial's 29.56. It falls within the common demonstration start distribution, so the earlier unusual initial gripper hypothesis does not explain this new failure on its own.

## Next change to investigate

First test synchronizing policy progression with body-joint tracking, so the next grasp/transport/release action does not advance while the previous body pose is substantially incomplete. Design and validate this offline before deploying a new physical trial. Merely removing the limiter can turn a large backlog into an abrupt movement; more training or longer runtime alone does not resolve a control-sequencing discrepancy. This report did not change robot limits, model weights, or inference control logic and did not start a second rollout.

Artifacts: `analysis.json`, `joint_commands_first25s.png`, `ceiling_oblique_first24s.png`, `ceiling_vertical_first24s.png`, and `successful_demo_ep{0,15}_oblique.jpg`.
