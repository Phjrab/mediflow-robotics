# Camera post-patch check

- User reported APPLIED_RESTART_REQUIRED: two source files patched, two timestamped backups created; LeLab user service active.
- HTTP observation only; no recording, robot control, serial access, training, or push performed.
- Wrist / end_effector: device 0, JPEG frames received, successive frame hashes differ.
- Oblique / ceiling_oblique: device 6, JPEG frames received, successive frame hashes differ. Previous uniform green image resolved. Image is dark with a color cast; color accuracy is not verified.
- Top / ceiling_vertical: device 8, snapshot sequence 1 to 33 in approximately one second. Preview includes a LIVE counter; snapshot additionally includes boxes/text. Raw recording output is not verified. Color accuracy is not verified.
- All three camera configurations: 640x480, requested 30 fps. Sustained achieved fps is not measured.
- Serial configuration warning: current LeLab configuration assigns leader to ttyACM0 and follower to ttyACM1. Earlier supplied code evidence assigned follower serial 5AE6058306 to ttyACM0. Current physical/by-id correspondence is unverified; do not infer a safe robot configuration from camera checks. Ports were not changed.
- Camera HTTP connections were closed after each sample. Server-side preview handle lifetime was not verified.
- Next: resolve/verify raw top image and color handling, verify physical serial identities before the user starts teleoperation, then record a short pilot manually and inspect saved data with check_new_recording.py.
