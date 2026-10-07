from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

from .dataset import load_jsonl, write_jsonl
from .model_utils import resolve_local_snapshot
from .schema import (
    ACTION_PROMPT,
    COMPACT_PROMPT,
    GRASP_PROMPT,
    extract_action_completion,
    extract_compact_completion,
    extract_json_object,
    extract_grasp_completion,
    validate_completion,
    validate_action_completion,
    validate_grasp_completion,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Qwen VLM inference on a prepared manifest.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("vlm_training/config/vlm.yaml"))
    parser.add_argument("--manifest", type=Path, default=Path("dataset_v2/test.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("dataset_v2/predictions.jsonl"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--adapter", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--answer-format",
        choices=("json", "compact", "compact-action", "compact-grasp"),
        default="json",
    )
    return parser


def _load_runtime(
    model_id: str,
    adapter: Path | None,
    load_in_4bit: bool,
    cache_dir: Path,
) -> tuple[Any, Any]:
    try:
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
    except ImportError as exc:
        raise SystemExit(
            "VLM dependencies are missing. Install vlm_training with the [train] extra."
        ) from exc

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model_source = resolve_local_snapshot(model_id, cache_dir)
    quantization_config = None
    if load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype,
        )
    model = AutoModelForMultimodalLM.from_pretrained(
        model_source,
        cache_dir=cache_dir,
        device_map="auto",
        dtype=compute_dtype,
        quantization_config=quantization_config,
    )
    if adapter is not None:
        try:
            from peft import PeftModel
        except ImportError as exc:
            raise SystemExit("PEFT is required to load an adapter") from exc
        model = PeftModel.from_pretrained(model, adapter)
    processor = AutoProcessor.from_pretrained(model_source, cache_dir=cache_dir)
    model.eval()
    return model, processor


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    config_path = args.config if args.config.is_absolute() else project_root / args.config
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model_id = args.model or config["model"]["id"]
    cache_dir = project_root / config["model"]["cache_dir"]
    max_new_tokens = int(config["model"]["max_new_tokens"])
    manifest_path = args.manifest if args.manifest.is_absolute() else project_root / args.manifest
    output_path = args.output if args.output.is_absolute() else project_root / args.output
    rows = load_jsonl(manifest_path)
    if args.limit is not None:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("manifest contains no samples")
    require_grasp_region = any(
        "grasp_region" in row.get("completion", {}) for row in rows
    )

    predictions: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    if args.resume and output_path.is_file():
        predictions = load_jsonl(output_path)
        completed_ids = {row["id"] for row in predictions}
        rows = [row for row in rows if row["id"] not in completed_ids]
        print(f"resuming with {len(completed_ids)} completed samples", flush=True)
    if not rows:
        print("all requested samples are already complete", flush=True)
        return

    adapter = args.adapter
    if adapter is not None and not adapter.is_absolute():
        adapter = project_root / adapter
    model, processor = _load_runtime(model_id, adapter, args.load_in_4bit, cache_dir)
    for index, row in enumerate(rows, 1):
        image_path = project_root / row["image"]
        with Image.open(image_path) as source_image:
            image = source_image.convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {
                        "type": "text",
                        "text": (
                            COMPACT_PROMPT
                            if args.answer_format == "compact"
                            else ACTION_PROMPT
                            if args.answer_format == "compact-action"
                            else GRASP_PROMPT
                            if args.answer_format == "compact-grasp"
                            else row["prompt"]
                        ),
                    },
                ],
            }
        ]
        prompt_text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = processor(text=[prompt_text], images=[image], return_tensors="pt")
        inputs = {key: value.to(model.device) for key, value in inputs.items()}
        output_ids = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = output_ids[0, inputs["input_ids"].shape[-1] :]
        raw_output = processor.decode(generated, skip_special_tokens=True).strip()
        prediction = None
        errors: list[str] = []
        try:
            if args.answer_format == "compact":
                prediction = extract_compact_completion(raw_output)
                valid, errors = validate_completion(
                    prediction, require_grasp_region=require_grasp_region
                )
            elif args.answer_format == "compact-action":
                prediction = extract_action_completion(raw_output)
                valid, errors = validate_action_completion(prediction)
            elif args.answer_format == "compact-grasp":
                prediction = extract_grasp_completion(raw_output)
                valid, errors = validate_grasp_completion(prediction)
            else:
                prediction = extract_json_object(raw_output)
                valid, errors = validate_completion(
                    prediction, require_grasp_region=require_grasp_region
                )
        except Exception as exc:
            valid = False
            errors = [str(exc)]
        predictions.append(
            {
                "id": row["id"],
                "model": model_id,
                "prediction": prediction,
                "raw_output": raw_output,
                "valid": valid,
                "errors": errors,
            }
        )
        write_jsonl(output_path, predictions)
        print(f"[{index}/{len(rows)}] {row['id']} valid={valid}", flush=True)


if __name__ == "__main__":
    main()
