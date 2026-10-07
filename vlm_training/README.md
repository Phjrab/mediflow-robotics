# MediFlow VLM training workspace

This directory is intentionally isolated from camera, calibration, ArUco, depth, and robot
control code. It never edits files under `data/pilot/`; it creates derived manifests under
`dataset_v2/` and model outputs under `work/`.

## Current scope

The first-stage model predicts only:

```json
{
  "medicine_id": "A",
  "orientation": "upright",
  "target_bin": "BIN_A"
}
```

`target_bin` is validated against a deterministic mapping. The current source dataset does
not provide trustworthy supervision for `graspable`, `required_action`, calibrated
`confidence`, or `reason`, so those fields are excluded.

## 1. Prepare `dataset_v2`

From the project root:

```bash
PYTHONPATH=vlm_training/src python -m mediflow_vlm.prepare_dataset
```

This command:

- verifies every source JSON record and image;
- selects `before_grasp` records only;
- replaces the answer-leaking medicine-specific instruction with a generic prompt;
- infers capture sessions from camera changes and time gaps;
- performs a session-level 70/15/15 split;
- marks perceptual near-duplicate and target-ambiguity candidates;
- keeps all generated rows in `review_status: pending` until human review.

Generated files:

```text
dataset_v2/
├── manifest.jsonl
├── train.jsonl
├── validation.jsonl
├── test.jsonl
└── qa_report.json
```

The manifests reference the original images; they do not copy or alter them.

For a conservative pipeline-only pilot that excludes near-duplicate candidates and
non-binary orientation labels while preserving the original manifests:

```bash
PYTHONPATH=vlm_training/src python -m mediflow_vlm.make_pilot
```

The generated `pilot_*` rows keep their original review status. They are not a
substitute for manual approval.

## 2. Environment check

Run these commands in a host terminal that has access to the RTX 3060:

```bash
nvidia-smi
/home/USER/miniconda3/envs/lerobot-3060/bin/python -c \
  'import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CUDA unavailable")'
```

The verified host configuration is RTX 3060, NVIDIA driver 595.84, and
PyTorch 2.11.0+cu130 with CUDA available.

Create an isolated environment rather than modifying the robot environment:

```bash
conda create -n mediflow-vlm python=3.12 -y
conda activate mediflow-vlm
python -m pip install --upgrade pip
python -m pip install -e './vlm_training[train,test]'
```

Install the CUDA-enabled PyTorch build appropriate for the host before the editable package
if the environment does not already contain PyTorch.

## 3. Validate the training gate

Training refuses to use pending samples by default:

```bash
mediflow-train --validate-only
```

After review, change only the derived manifest records from `pending` to `approved`. For an
explicit exploratory run, `--allow-pending-review` bypasses this gate and is recorded in the
startup summary.

## 3.1 Review labels in the local UI

Start the review server from the project root:

```bash
work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.review_app
```

Open `http://127.0.0.1:8020/`. Decisions are appended to
`dataset_v2/review_decisions.jsonl`; the source manifest and source images are never
edited. The export button writes approved records, including corrections, to
`dataset_v2/reviewed_train.jsonl`, `reviewed_validation.jsonl`, and
`reviewed_test.jsonl`.

The review decision also stores `grasp_region` as `lid`, `body`, or `unknown`.
It is exported under the `review` metadata and intentionally stays outside the
current three-field VLM completion schema.

New captures synced from the Jetson can be imported without changing
`data/pilot/`:

```bash
mediflow-import-capture
```

The importer verifies that the staged dataset begins with the immutable pilot
baseline, copies only appended images to `data/imported/jetson_20260923/`, and
creates `dataset_v2/review_manifest.jsonl`. Imported rows appear under the
`신규 촬영` filter.

## 4. Baseline inference

Run a small deterministic zero-shot sample before training:

```bash
mediflow-infer \
  --manifest dataset_v2/test.jsonl \
  --output dataset_v2/predictions-zero-shot.jsonl \
  --load-in-4bit \
  --limit 30

mediflow-evaluate \
  --manifest dataset_v2/test.jsonl \
  --predictions dataset_v2/predictions-zero-shot.jsonl \
  --output dataset_v2/evaluation-zero-shot.json
```

## 5. QLoRA training

After manual review and after confirming that CUDA works:

```bash
mediflow-train \
  --train-manifest dataset_v2/train.jsonl \
  --validation-manifest dataset_v2/validation.jsonl \
  --output work/qwen3-vl-2b-mediflow-lora
```

For the four-field v3 dataset, compact mode concentrates the loss on the three
learned labels and derives `target_bin` deterministically during inference:

```bash
mediflow-train \
  --config vlm_training/config/vlm_v3.yaml \
  --train-manifest dataset_v3/train.jsonl \
  --validation-manifest dataset_v3/validation.jsonl \
  --balance-strategy medicine-grasp \
  --answer-format compact
```

Use `--resume-from-checkpoint PATH` to continue optimizer, scheduler, and RNG
state without repeating completed epochs. Pass the same
`--answer-format compact` flag to `mediflow-infer`; its prediction file still
contains the normal four-field JSON schema.

When the medicine is already specified by the robot command, train only the two
image-owned fields:

```bash
mediflow-train \
  --config vlm_training/config/vlm_v3.yaml \
  --train-manifest dataset_v3/train.jsonl \
  --validation-manifest dataset_v3/validation.jsonl \
  --balance-strategy orientation-grasp \
  --answer-format compact-action
```

Inference uses the same `--answer-format compact-action` option and returns
`orientation` plus `grasp_region`. Use `compose_command_action()` to combine
those image-owned labels with the command-owned `medicine_id`; `target_bin` is
then derived deterministically.

The selected runtime now uses two small adapters over the same loaded base model:

- orientation: `work/qwen3-vl-2b-mediflow-action-v1-20260926/checkpoint-47`
- grasp region: `work/qwen3-vl-2b-mediflow-grasp-v1-20260926/checkpoint-47`

The grasp-only adapter was retained because it improved held-out grasp accuracy
from 73.0% to 81.1% and `unknown` recall from 18.2% to 68.2%. Run an offline,
single-image decision with both adapters as follows:

```bash
work/venvs/mediflow-vlm/bin/python -m mediflow_vlm.safe_infer \
  --project-root . \
  --config vlm_training/config/vlm_v3.yaml \
  --image PATH_TO_IMAGE \
  --medicine-id B \
  --camera /dev/video6 \
  --capture-phase before_grasp \
  --load-in-4bit \
  --output work/safe-infer-result.json
```

This command performs inference only. It never controls the robot, and its
safety result always has `robot_motion_allowed: false`. Only `/dev/video6` plus
`before_grasp` is eligible for human review; all other camera/phase combinations
are blocked by the current fail-closed policy.

Evaluate the adapter on the untouched test split:

```bash
mediflow-infer \
  --manifest dataset_v2/test.jsonl \
  --adapter work/qwen3-vl-2b-mediflow-lora/final_adapter \
  --output dataset_v2/predictions-lora.jsonl \
  --load-in-4bit

mediflow-evaluate \
  --manifest dataset_v2/test.jsonl \
  --predictions dataset_v2/predictions-lora.jsonl \
  --output dataset_v2/evaluation-lora.json
```

## Safety boundaries

- No command in this directory controls SO-ARM101.
- No command starts the Jetson camera services.
- No source image or source annotation is modified or deleted.
- Invalid JSON, unknown medicine, and inconsistent medicine-to-bin mappings must fail closed.
- Model evaluation is not authorization to move the robot.
- A `review_required` result is not an execution approval; it still requires a
  person to check the image and decision.
