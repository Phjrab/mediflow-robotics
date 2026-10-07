from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from .dataset import load_jsonl
from .model_utils import resolve_local_snapshot
from .schema import (
    ACTION_PROMPT,
    COMPACT_PROMPT,
    GRASP_PROMPT,
    format_action_completion,
    format_compact_completion,
    format_grasp_completion,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a 4-bit QLoRA adapter for Qwen3-VL.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("vlm_training/config/vlm.yaml"))
    parser.add_argument("--train-manifest", type=Path, default=Path("dataset_v2/train.jsonl"))
    parser.add_argument(
        "--validation-manifest", type=Path, default=Path("dataset_v2/validation.jsonl")
    )
    parser.add_argument("--output", type=Path, default=Path("work/qwen3-vl-2b-mediflow-lora"))
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument(
        "--balance-strategy",
        choices=("none", "medicine-grasp", "orientation-grasp", "grasp"),
        default="none",
        help="Deterministically resample one epoch to balance medicine/grasp combinations.",
    )
    parser.add_argument(
        "--answer-format",
        choices=("json", "compact", "compact-action", "compact-grasp"),
        default="json",
        help="Train full JSON or a compact label-only response.",
    )
    parser.add_argument("--allow-pending-review", action="store_true")
    parser.add_argument(
        "--resume-from-checkpoint",
        type=Path,
        default=None,
        help="Continue optimizer, scheduler, and RNG state from a Trainer checkpoint.",
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _resolve(project_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def _validate_training_rows(rows: list[dict[str, Any]], allow_pending: bool) -> None:
    if not rows:
        raise SystemExit("training manifest is empty")
    pending = [row["id"] for row in rows if row.get("review_status") != "approved"]
    if pending and not allow_pending:
        raise SystemExit(
            f"{len(pending)} samples are not approved. Complete manual review or pass "
            "--allow-pending-review explicitly."
        )


def _to_hf_dataset(
    rows: list[dict[str, Any]], project_root: Path, answer_format: str = "json"
) -> Any:
    from datasets import Dataset, Image as HFImage

    samples = []
    for row in rows:
        if answer_format == "compact":
            prompt = COMPACT_PROMPT
            completion = format_compact_completion(row["completion"])
        elif answer_format == "compact-action":
            prompt = ACTION_PROMPT
            completion = format_action_completion(row["completion"])
        elif answer_format == "compact-grasp":
            prompt = GRASP_PROMPT
            completion = format_grasp_completion(row["completion"])
        elif answer_format == "json":
            prompt = row["prompt"]
            completion = json.dumps(row["completion"], ensure_ascii=False, sort_keys=True)
        else:
            raise ValueError(f"unsupported answer format: {answer_format}")
        samples.append(
            {
                "image": str((project_root / row["image"]).resolve()),
                "prompt": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image"},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "completion": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": completion}],
                    }
                ],
            }
        )
    return Dataset.from_list(samples).cast_column("image", HFImage())


def _balance_training_rows(
    rows: list[dict[str, Any]], strategy: str, seed: int
) -> list[dict[str, Any]]:
    """Return a same-size deterministic epoch with balanced label combinations."""
    if strategy == "none":
        return list(rows)
    if strategy not in {"medicine-grasp", "orientation-grasp", "grasp"}:
        raise ValueError(f"unsupported balance strategy: {strategy}")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        completion = row.get("completion", {})
        if strategy == "grasp":
            key = (str(completion.get("grasp_region")),)
        else:
            first_field = "medicine_id" if strategy == "medicine-grasp" else "orientation"
            key = (str(completion.get(first_field)), str(completion.get("grasp_region")))
        groups[key].append(row)
    if not groups:
        return []

    rng = random.Random(seed)
    keys = sorted(groups)
    for group in groups.values():
        rng.shuffle(group)
    quotas = {key: len(rows) // len(keys) for key in keys}
    for key in keys[: len(rows) % len(keys)]:
        quotas[key] += 1

    balanced: list[dict[str, Any]] = []
    for key in keys:
        group = groups[key]
        balanced.extend(group[index % len(group)] for index in range(quotas[key]))
    rng.shuffle(balanced)
    return balanced


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    config_path = _resolve(project_root, args.config)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    train_rows = load_jsonl(_resolve(project_root, args.train_manifest))
    validation_rows = load_jsonl(_resolve(project_root, args.validation_manifest))
    _validate_training_rows(train_rows + validation_rows, args.allow_pending_review)

    session_overlap = {row["session_id"] for row in train_rows} & {
        row["session_id"] for row in validation_rows
    }
    if session_overlap:
        raise SystemExit(f"session leakage between train and validation: {sorted(session_overlap)}")
    split_seed = int(config["dataset"]["split_seed"])
    balanced_train_rows = _balance_training_rows(
        train_rows, args.balance_strategy, split_seed
    )
    print(
        json.dumps(
            {
                "train_samples": len(train_rows),
                "effective_train_samples": len(balanced_train_rows),
                "validation_samples": len(validation_rows),
                "model": config["model"]["id"],
                "review_override": args.allow_pending_review,
                "completion_only_loss": True,
                "balance_strategy": args.balance_strategy,
                "answer_format": args.answer_format,
                "resume_from_checkpoint": (
                    str(args.resume_from_checkpoint)
                    if args.resume_from_checkpoint is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.validate_only:
        return

    try:
        import torch
        from peft import LoraConfig, prepare_model_for_kbit_training
        from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise SystemExit(
            "Training dependencies are missing. Install vlm_training with the [train] extra."
        ) from exc
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable; refusing to start QLoRA training on CPU")

    model_config = config["model"]
    model_id = model_config["id"]
    cache_dir = project_root / model_config["cache_dir"]
    model_source = resolve_local_snapshot(model_id, cache_dir)
    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(
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
        quantization_config=quantization,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    processor = AutoProcessor.from_pretrained(model_source, cache_dir=cache_dir)
    lora = LoraConfig(
        r=int(model_config["lora_rank"]),
        lora_alpha=int(model_config["lora_alpha"]),
        lora_dropout=float(model_config["lora_dropout"]),
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        bias="none",
        task_type="CAUSAL_LM",
    )
    output_path = _resolve(project_root, args.output)
    training_args = SFTConfig(
        output_dir=str(output_path),
        max_length=None,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=compute_dtype == torch.bfloat16,
        fp16=compute_dtype == torch.float16,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        logging_steps=5,
        remove_unused_columns=False,
        report_to="none",
        seed=split_seed,
        data_seed=split_seed,
        completion_only_loss=True,
    )
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=_to_hf_dataset(
            balanced_train_rows, project_root, args.answer_format
        ),
        eval_dataset=_to_hf_dataset(validation_rows, project_root, args.answer_format),
        processing_class=processor,
        peft_config=lora,
    )
    resume_checkpoint = args.resume_from_checkpoint
    if resume_checkpoint is not None:
        resume_checkpoint = _resolve(project_root, resume_checkpoint)
        if not (resume_checkpoint / "trainer_state.json").is_file():
            raise SystemExit(f"invalid trainer checkpoint: {resume_checkpoint}")
    trainer.train(
        resume_from_checkpoint=str(resume_checkpoint) if resume_checkpoint else None
    )
    trainer.save_model(str(output_path / "final_adapter"))
    processor.save_pretrained(str(output_path / "final_adapter"))


if __name__ == "__main__":
    main()
