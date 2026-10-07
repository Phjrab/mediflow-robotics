from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

from .infer import _load_runtime
from .schema import (
    ACTION_PROMPT,
    BIN_MAP,
    GRASP_PROMPT,
    compose_command_action,
    extract_action_completion,
    extract_grasp_completion,
)
from .safety import assess_action_safety


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one image through the action VLM without controlling the robot."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("vlm_training/config/vlm_v3.yaml"))
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--medicine-id", choices=("A", "B", "C"), required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument(
        "--capture-phase", choices=("before_grasp", "after_grasp"), required=True
    )
    parser.add_argument(
        "--action-adapter",
        "--adapter",
        dest="action_adapter",
        type=Path,
        default=Path("work/qwen3-vl-2b-mediflow-action-v1-20260926/checkpoint-47"),
    )
    parser.add_argument(
        "--grasp-adapter",
        type=Path,
        default=Path("work/qwen3-vl-2b-mediflow-grasp-v1-20260926/checkpoint-47"),
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--load-in-4bit", action="store_true")
    return parser


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def predict_label(
    model: Any,
    processor: Any,
    image: Image.Image,
    prompt_text: str,
    max_new_tokens: int,
) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt_text},
            ],
        }
    ]
    prompt = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = processor(text=[prompt], images=[image], return_tensors="pt")
    inputs = {key: value.to(model.device) for key, value in inputs.items()}
    output_ids = model.generate(
        **inputs, max_new_tokens=max_new_tokens, do_sample=False
    )
    generated = output_ids[0, inputs["input_ids"].shape[-1] :]
    return processor.decode(generated, skip_special_tokens=True).strip()


def main() -> None:
    args = build_parser().parse_args()
    root = args.project_root.resolve()
    config = yaml.safe_load(_resolve(root, args.config).read_text(encoding="utf-8"))
    image_path = _resolve(root, args.image)
    action_adapter = _resolve(root, args.action_adapter)
    grasp_adapter = _resolve(root, args.grasp_adapter)
    cache_dir = root / config["model"]["cache_dir"]
    model, processor = _load_runtime(
        config["model"]["id"], action_adapter, args.load_in_4bit, cache_dir
    )
    model.load_adapter(grasp_adapter, adapter_name="grasp")
    model.eval()
    with Image.open(image_path) as source:
        image = source.convert("RGB")

    max_new_tokens = int(config["model"]["max_new_tokens"])
    model.set_adapter("default")
    raw_action = predict_label(
        model, processor, image, ACTION_PROMPT, max_new_tokens
    )
    model.set_adapter("grasp")
    raw_grasp = predict_label(
        model, processor, image, GRASP_PROMPT, max_new_tokens
    )
    action = None
    action_component = None
    grasp_component = None
    decision = None
    errors: list[str] = []
    try:
        action_component = extract_action_completion(raw_action)
    except Exception as exc:
        errors.append(f"action adapter: {exc}")
    try:
        grasp_component = extract_grasp_completion(raw_grasp)
    except Exception as exc:
        errors.append(f"grasp adapter: {exc}")
    if action_component is not None and grasp_component is not None:
        action = {
            "orientation": action_component["orientation"],
            "grasp_region": grasp_component["grasp_region"],
        }
    try:
        if action is None:
            raise ValueError("one or more prediction components are invalid")
        decision = compose_command_action(args.medicine_id, action)
    except Exception as exc:
        errors.append(f"combined decision: {exc}")
    safety = assess_action_safety(
        action, camera=args.camera, capture_phase=args.capture_phase
    )
    result = {
        "image": str(image_path),
        "command": {
            "medicine_id": args.medicine_id,
            "target_bin": BIN_MAP[args.medicine_id],
        },
        "component_predictions": {
            "action_adapter": action_component,
            "grasp_adapter": grasp_component,
        },
        "action_prediction": action,
        "decision": decision,
        "raw_output": {
            "action_adapter": raw_action,
            "grasp_adapter": raw_grasp,
        },
        "errors": errors,
        "safety": safety,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        output_path = _resolve(root, args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
