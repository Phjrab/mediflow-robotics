from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from .dataset import (
    file_sha256,
    load_jsonl,
    prepare_rows,
    validate_source_rows,
    write_jsonl,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare an immutable-reference dataset_v2 manifest from pilot JSONL."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--config", type=Path, default=Path("vlm_training/config/vlm.yaml")
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = args.project_root.resolve()
    config_path = args.config
    if not config_path.is_absolute():
        config_path = project_root / config_path
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    dataset_config = config["dataset"]
    annotations = project_root / dataset_config["annotations"]
    dataset_root = project_root / dataset_config["root"]
    output = project_root / dataset_config["output"]
    rows = load_jsonl(annotations)
    errors = validate_source_rows(rows, dataset_root)
    if errors:
        preview = "\n".join(errors[:20])
        raise SystemExit(f"source validation failed with {len(errors)} errors:\n{preview}")

    prepared = prepare_rows(
        source_rows=rows,
        dataset_root=dataset_root,
        project_root=project_root,
        prompt=config["prompt"],
        phase=dataset_config["phase"],
        gap_seconds=float(dataset_config["session_gap_seconds"]),
        fractions=dict(dataset_config["splits"]),
        seed=int(dataset_config["split_seed"]),
    )
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "manifest.jsonl", prepared.rows)
    for split in dataset_config["splits"]:
        write_jsonl(
            output / f"{split}.jsonl",
            (row for row in prepared.rows if row["split"] == split),
        )

    report = dict(prepared.report)
    report["source_annotations"] = str(annotations.relative_to(project_root))
    report["source_annotations_sha256"] = file_sha256(annotations)
    report["config"] = str(config_path.relative_to(project_root))
    report_path = output / "qa_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

