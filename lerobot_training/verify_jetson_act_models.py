#!/usr/bin/env python3
"""No-motion CUDA and ACT checkpoint verification for the Jetson runtime."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path

import torch

from lerobot.policies.act.modeling_act import ACTPolicy


EXPECTED_INPUTS = {
    "observation.images.ceiling_oblique",
    "observation.images.ceiling_vertical",
    "observation.images.end_effector",
    "observation.state",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("/home/USER/so101-medicine-bootstrap/models"),
    )
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available in this runtime")

    test = torch.ones((64, 64), device="cuda")
    cuda_sum = float((test @ test).sum())
    torch.cuda.synchronize()
    print(
        f"CUDA OK: torch={torch.__version__} "
        f"device={torch.cuda.get_device_name(0)} matmul_sum={cuda_sum}"
    )

    for label in ("a", "b", "c"):
        model_path = args.model_root / f"act-{label}-3cam-10k"
        policy = ACTPolicy.from_pretrained(model_path).to("cuda")
        inputs = set(policy.config.input_features)
        if inputs != EXPECTED_INPUTS:
            raise RuntimeError(f"{label.upper()} input mismatch: {sorted(inputs)}")

        state = policy.state_dict()
        if not all(torch.isfinite(tensor).all().item() for tensor in state.values()):
            raise RuntimeError(f"{label.upper()} contains NaN or Inf")

        parameter_count = sum(tensor.numel() for tensor in policy.parameters())
        state_element_count = sum(tensor.numel() for tensor in state.values())
        print(
            f"{label.upper()} OK: parameters={parameter_count} "
            f"state_elements={state_element_count} device={next(policy.parameters()).device}"
        )
        del state, policy
        gc.collect()
        torch.cuda.empty_cache()

    print("PASS: CUDA and A/B/C model-load checks completed; no inference or robot I/O was run.")


if __name__ == "__main__":
    main()
