"""Approved single-arm X-VLA bundle loader for offline CPU use."""

from dataclasses import dataclass
import hashlib
from itertools import chain
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from xvla_adapter import Contract, JOINTS, RGBFrame, XVLAAdapter, batch_one_chunk, finite_vector

UNITS = ("degree",) * 5 + ("point_0_to_100",)
PRE = "checkpoint/policy_preprocessor.json"
POST = "checkpoint/policy_postprocessor.json"
CONFIG = "checkpoint/config.json"
WEIGHTS = "checkpoint/model.safetensors"
TRAIN_FEATURES = "training/meta/info.json"
GOLDEN = "golden.json"
GOLDEN_TOLERANCE = 1e-4
PRE_STEPS = (
    "rename_observations_processor", "to_batch_processor", "tokenizer_processor",
    "xvla_image_to_float", "xvla_imagenet_normalize", "xvla_add_domain_id",
    "device_processor", "normalizer_processor",
)
POST_STEPS = ("unnormalizer_processor", "device_processor")


class BundleError(ValueError):
    """The bundle cannot be loaded or its action contract failed."""


def require(condition, message):
    if not condition:
        raise BundleError(message)


def unique_keys(pairs):
    obj = dict(pairs)
    require(len(obj) == len(pairs), "duplicate JSON key")
    return obj


def read_json(data):
    return json.loads(data, object_pairs_hook=unique_keys)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_file(root, name, digest):
    require(isinstance(name, str) and ":" not in name and "\\" not in name, "invalid artifact path")
    relative = PurePosixPath(name)
    require(
        not relative.is_absolute() and name == relative.as_posix() and ".." not in relative.parts,
        f"invalid artifact path: {name}",
    )
    path = root.joinpath(*relative.parts)
    require(path.is_file() and path.resolve() == path, f"missing or linked artifact: {name}")
    require(re.fullmatch(r"[0-9a-f]{64}", digest or "") and sha256(path) == digest,
            f"SHA-256 mismatch: {name}")
    return path


@dataclass(frozen=True)
class VerifiedBundle:
    root: Path
    contract: Contract
    runtime: dict
    model_input: tuple
    expected: tuple


def checked_steps(data, expected, paths):
    steps = data["steps"]
    require(tuple(step.get("registry_name") for step in steps) == expected
            and not any("class" in step for step in steps), "unsupported processor step sequence")
    by_name = {step["registry_name"]: step for step in steps}
    for step in steps:
        if "state_file" in step:
            name = step["state_file"]
            require(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_.-]+\.safetensors", name)
                    and f"checkpoint/{name}" in paths, "unbound processor statistics")
    return by_name


def checked_golden(golden, horizon):
    raw = batch_one_chunk(golden["normalized_chunk"], horizon)
    rows = golden["expected_by_joint"]
    require(isinstance(rows, list) and len(rows) == horizon, "golden horizon mismatch")
    expected = []
    for raw_row, row in zip(raw, rows, strict=True):
        require(isinstance(row, dict) and set(row) == set(JOINTS), "golden must name all six axes")
        target = finite_vector([row[joint] for joint in JOINTS])
        for values in (raw_row, target):
            ordered = sorted(values)
            require(all(b - a > 2 * GOLDEN_TOLERANCE for a, b in zip(ordered, ordered[1:])),
                    "golden axes must be distinguishable")
        expected.append(target)
    return raw, tuple(expected)


def inspect_bundle(directory, approved_manifest_sha256):
    """Validate artifacts before importing model code or creating a backend."""
    try:
        return _inspect_bundle(Path(directory).resolve(strict=True), approved_manifest_sha256)
    except BundleError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise BundleError(f"invalid X-VLA bundle: {exc}") from exc


def _inspect_bundle(root, approval):
    manifest_file = root / "approved_manifest.json"
    require(
        isinstance(approval, str) and re.fullmatch(r"[0-9a-f]{64}", approval)
        and manifest_file.is_file() and manifest_file.resolve() == manifest_file,
        "approved manifest and its independently supplied SHA-256 are required",
    )
    manifest_bytes = manifest_file.read_bytes()
    require(hashlib.sha256(manifest_bytes).hexdigest() == approval, "approved manifest SHA-256 mismatch")
    manifest = read_json(manifest_bytes)
    require(manifest["schema_version"] == 1, "unsupported manifest schema")
    for key in ("training_run_id", "training_dataset_revision", "training_feature_provenance"):
        require(isinstance(manifest[key], str) and manifest[key].strip(), f"missing {key}")

    axes = manifest["training_action_axes"]
    require(
        axes == [
            {"name": name, "unit": unit, "meaning": "absolute_position_target"}
            for name, unit in zip(JOINTS, UNITS, strict=True)
        ],
        "training action axis name, order, unit or absolute meaning mismatch",
    )
    inventory = manifest["files"]
    for name in (CONFIG, WEIGHTS, PRE, POST, TRAIN_FEATURES, GOLDEN):
        require(name in inventory, f"missing required artifact digest: {name}")
    paths = {name: verified_file(root, name, digest) for name, digest in inventory.items()}
    tokenizer_dir = root / "tokenizer"
    tokenizer_files = {name for name in paths if name.startswith("tokenizer/")}
    actual_tokenizer_files = {
        path.relative_to(root).as_posix() for path in tokenizer_dir.rglob("*") if path.is_file()
    }
    require(
        {"tokenizer/tokenizer_config.json", "tokenizer/tokenizer.json"} <= tokenizer_files
        and tokenizer_files == actual_tokenizer_files,
        "local tokenizer files must be fully hash-bound",
    )

    def document(name):
        return read_json(paths[name].read_bytes())

    dataset = document(TRAIN_FEATURES)
    for key in ("action", "observation.state"):
        feature = dataset["features"][key]
        require(feature["shape"] == [6] and feature["names"] == list(JOINTS)
                and feature["dtype"] == "float32", f"actual training {key} names/order/type mismatch")
    action = dataset["features"]["action"]
    require(action.get("units", list(UNITS)) == list(UNITS)
            and action.get("meaning", "absolute_position_target") == "absolute_position_target",
            "training feature units or action meaning contradict approved contract")

    config = document(CONFIG)
    require(
        config["type"] == "xvla" and config["action_mode"] == "auto"
        and config["output_features"]["action"] == {"type": "ACTION", "shape": [6]}
        and config["input_features"]["observation.state"] == {"type": "STATE", "shape": [6]}
        and config.get("max_action_dim", 20) == 20
        and config.get("use_proprio", True) is True
        and config.get("n_obs_steps", 1) == 1
        and config.get("empty_cameras", 0) == 0
        and config.get("num_image_views", 3) in (None, 3)
        and config.get("domain_feature_key") in (None, "domain_id"),
        "checkpoint must be a three-view single-arm X-VLA auto/6 model",
    )
    cameras = tuple(manifest["camera_keys_wrist_overhead_oblique"])
    require(tuple(k for k, v in config["input_features"].items() if v["type"] == "VISUAL") == cameras,
            "checkpoint camera feature order mismatch")

    pre = checked_steps(document(PRE), PRE_STEPS, paths)
    post = checked_steps(document(POST), POST_STEPS, paths)
    rename = pre["rename_observations_processor"]["config"].get("rename_map", {})
    require(all(key == value for key, value in rename.items()), "processor unexpectedly remaps camera/state names")
    for registry in ("xvla_image_to_float", "xvla_imagenet_normalize"):
        keys = pre[registry]["config"].get("image_keys")
        require(keys is None or keys == list(cameras), f"{registry} camera keys mismatch")
    domain = manifest["domain_id"]
    require(type(domain) is int and pre["xvla_add_domain_id"]["config"]["domain_id"] == domain,
            "processor domain ID mismatch")
    require(pre["tokenizer_processor"]["config"].get("task_key", "task") == "task",
            "processor task key mismatch")
    for stages, name in ((pre, "normalizer_processor"), (post, "unnormalizer_processor")):
        step = stages[name]
        normalization = step["config"]["norm_map"]["ACTION"]
        require(
            normalization == config["normalization_mapping"]["ACTION"]
            and normalization in ("IDENTITY", "MEAN_STD")
            and step["config"]["features"]["action"] == config["output_features"]["action"],
            "processor action normalization or feature mismatch",
        )
        if normalization == "MEAN_STD":
            require("state_file" in step, "MEAN_STD requires hash-bound processor statistics")

    horizon = config["chunk_size"]
    execution = config["n_action_steps"]
    require(type(horizon) is int and type(execution) is int and dataset["fps"] == manifest["action_fps"],
            "invalid horizon or action fps")
    contract = Contract(cameras, horizon, execution, manifest["action_fps"], domain,
                        tuple(tuple(bounds) for bounds in manifest["bounds_by_axis"]), approval)
    raw, expected = checked_golden(document(GOLDEN), horizon)
    runtime = manifest["runtime"]
    require(set(runtime) == {"lerobot", "torch", "transformers", "safetensors"}
            and all(isinstance(pin, str) and pin.strip() for pin in runtime.values()),
            "exact CPU runtime versions are required")
    return VerifiedBundle(root, contract, runtime, raw, expected)


class OfficialCPUBridge:
    def __init__(self, torch, policy, preprocessor, postprocessor):
        self.torch = torch
        self.policy = policy
        self.preprocessor = preprocessor
        self.postprocessor = postprocessor

    def reset(self):
        for component in (self.policy, self.preprocessor, self.postprocessor):
            component.reset()

    def tensor_chunk(self, rows):
        return self.torch.tensor([rows], dtype=self.torch.float32, device="cpu")

    def preprocess(self, observation):
        batch = {}
        for key, value in observation.items():
            if isinstance(value, RGBFrame):
                pixels = self.torch.frombuffer(bytearray(value.packed_rgb), dtype=self.torch.uint8)
                batch[key] = pixels.reshape(value.height, value.width, 3).permute(2, 0, 1).contiguous()
            elif key == "observation.state":
                batch[key] = self.torch.tensor(value, dtype=self.torch.float32, device="cpu")
            else:
                batch[key] = value
        return self.preprocessor(batch)

    def predict_action_chunk(self, batch):
        with self.torch.inference_mode():
            return self.policy.predict_action_chunk(batch)

    def postprocess(self, policy_chunk):
        return self.postprocessor(policy_chunk)


def verify_decode(adapter, bridge, bundle):
    decoded = adapter.decode_actions(bridge.tensor_chunk(bundle.model_input))
    for i, (actual, expected) in enumerate(zip(decoded, bundle.expected, strict=True)):
        for joint, value, target in zip(JOINTS, actual, expected, strict=True):
            require(math.isclose(value, target, abs_tol=GOLDEN_TOLERANCE, rel_tol=0),
                    f"golden axis mismatch at row {i}: {joint}")


def guarded_adapter(bundle, bridge):
    adapter = XVLAAdapter.load(bundle.contract, bridge)
    verify_decode(adapter, bridge, bundle)
    adapter.reset()
    return adapter


def load_cpu_config(checkpoint):
    from lerobot.configs import PreTrainedConfig
    from lerobot.policies.xvla.configuration_xvla import XVLAConfig

    config = PreTrainedConfig.from_pretrained(
        checkpoint, local_files_only=True, cli_overrides=["--device=cpu", "--use_amp=false"],
    )
    require(isinstance(config, XVLAConfig) and str(config.device) == "cpu" and not config.use_amp,
            "checkpoint did not resolve to X-VLA on CPU with AMP disabled")
    return config


def load_saved_postprocessor(checkpoint):
    from lerobot.processor import (
        PolicyProcessorPipeline, policy_action_to_transition, transition_to_policy_action,
    )

    return PolicyProcessorPipeline.from_pretrained(
        checkpoint, config_filename=Path(POST).name, local_files_only=True,
        overrides={"device_processor": {"device": "cpu"}},
        to_transition=policy_action_to_transition, to_output=transition_to_policy_action,
    )


def load_cpu_bundle(directory, approved_manifest_sha256):
    """Return the adapter only after the active decode path passes its approved golden test."""
    bundle = inspect_bundle(directory, approved_manifest_sha256)
    for package, expected in bundle.runtime.items():
        try:
            found = version(package)
        except PackageNotFoundError as exc:
            raise BundleError(f"CPU runtime dependency missing: {package}") from exc
        require(found == expected, f"CPU runtime version mismatch: {package} {found} != {expected}")

    import torch
    from lerobot.policies.xvla.modeling_xvla import XVLAPolicy
    from lerobot.policies.xvla import processor_xvla  # Registers X-VLA processor steps.
    from lerobot.processor import (
        PolicyProcessorPipeline, batch_to_transition,
        transition_to_batch,
    )

    checkpoint = bundle.root / "checkpoint"
    config = load_cpu_config(checkpoint)
    pre = PolicyProcessorPipeline.from_pretrained(
        checkpoint, config_filename=Path(PRE).name, local_files_only=True,
        overrides={
            "device_processor": {"device": "cpu"},
            "tokenizer_processor": {"tokenizer_name": str(bundle.root / "tokenizer")},
        },
        to_transition=batch_to_transition, to_output=transition_to_batch,
    )
    post = load_saved_postprocessor(checkpoint)
    policy = XVLAPolicy.from_pretrained(checkpoint, config=config, local_files_only=True, strict=True)
    require(
        all(tensor.device.type == "cpu" for tensor in chain(policy.parameters(), policy.buffers())),
        "policy tensors are not on CPU",
    )
    return guarded_adapter(bundle, OfficialCPUBridge(torch, policy, pre, post))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("approved_manifest_sha256")
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES="-1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    adapter = load_cpu_bundle(args.bundle, args.approved_manifest_sha256)
    print("PASS: CPU bundle loaded and golden decode verified")
    print(adapter.metadata())
