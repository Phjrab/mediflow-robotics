from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from xvla_adapter import JOINTS

from . import xvla_bundle as loader

CAMERAS = ["observation.images." + name for name in ("hand", "top", "side")]
ACTION = {"type": "ACTION", "shape": [6]}
MEANS = (10, 20, 30, 40, 50, 60)
STDS = (1, 2, 3, 4, 5, 1)
RAW = [[1, 2, 3, 4, 5, 6], [-1, -2, -3, -4, -5, -6]]
EXPECTED = [[11, 24, 39, 56, 75, 66], [9, 16, 21, 24, 25, 54]]
PRE_STATS = "pre.safetensors"
POST_STATS = "post.safetensors"


def swapped(chunk):
    return [[(row[1], row[0], *row[2:]) for row in chunk[0]]]


class FixtureBridge:
    def reset(self):
        pass

    def tensor_chunk(self, rows):
        return [rows]

    def postprocess(self, values):
        return [
            [tuple(x * std + mean for x, std, mean in zip(row, STDS, MEANS, strict=True))
             for row in values[0]]
        ]


class BundleFiles:
    def __init__(self, root):
        self.root = root
        self.manifest = {
            "schema_version": 1,
            "training_run_id": "synthetic",
            "training_dataset_revision": "synthetic",
            "training_feature_provenance": "synthetic; no training run",
            "training_action_axes": [
                {"name": name, "unit": unit, "meaning": "absolute_position_target"}
                for name, unit in zip(JOINTS, loader.UNITS, strict=True)
            ],
            "camera_keys_wrist_overhead_oblique": CAMERAS,
            "domain_id": 0,
            "action_fps": 30,
            "bounds_by_axis": [[-120, 120]] * 5 + [[0, 100]],
            "runtime": dict.fromkeys(("lerobot", "torch", "transformers", "safetensors"), "fixture"),
        }
        self.write_json(loader.CONFIG, {
            "type": "xvla", "action_mode": "auto", "chunk_size": 2, "n_action_steps": 2,
            "input_features": {
                "observation.state": {"type": "STATE", "shape": [6]},
                **{key: {"type": "VISUAL", "shape": [3, 4, 5]} for key in CAMERAS},
            },
            "output_features": {"action": ACTION},
            "normalization_mapping": {"ACTION": "MEAN_STD"},
        })
        feature = {"dtype": "float32", "shape": [6], "names": list(JOINTS)}
        self.write_json(loader.TRAIN_FEATURES, {
            "features": {"action": feature, "observation.state": feature}, "fps": 30,
        })
        self.write_json(loader.GOLDEN, {
            "normalized_chunk": [RAW],
            "expected_by_joint": [dict(zip(JOINTS, row, strict=True)) for row in EXPECTED],
        })
        norm_config = {"features": {"action": ACTION}, "norm_map": {"ACTION": "MEAN_STD"}}
        configs = {
            "xvla_add_domain_id": {"domain_id": 0},
            "device_processor": {"device": "cpu"},
            "normalizer_processor": norm_config,
        }
        steps = [{"registry_name": key, "config": configs.get(key, {})} for key in loader.PRE_STEPS]
        steps[-1]["state_file"] = PRE_STATS
        self.write_json(loader.PRE, {"name": "policy_preprocessor", "steps": steps})
        self.write_json(loader.POST, {"name": "policy_postprocessor", "steps": [
            {"registry_name": "unnormalizer_processor", "config": norm_config, "state_file": POST_STATS},
            {"registry_name": "device_processor", "config": {"device": "cpu"}},
        ]})
        for name in (loader.WEIGHTS, f"checkpoint/{PRE_STATS}", f"checkpoint/{POST_STATS}",
                     "tokenizer/tokenizer_config.json", "tokenizer/tokenizer.json"):
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"fixture only")
        self.approve()

    def write_json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def document(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def approve(self, omit=()):
        self.manifest["files"] = {
            path.relative_to(self.root).as_posix(): loader.sha256(path)
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "approved_manifest.json"
            and path.relative_to(self.root).as_posix() not in omit
        }
        self.write_json("approved_manifest.json", self.manifest)
        self.digest = loader.sha256(self.root / "approved_manifest.json")

    def inspect(self):
        return loader.inspect_bundle(self.root, self.digest)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.files = BundleFiles(Path(directory.name))

    def test_selected_decode_path_accepts_distinct_canonical_values(self):
        adapter = loader.guarded_adapter(self.files.inspect(), FixtureBridge())
        self.assertEqual(adapter.metadata()["joint_order"], JOINTS)

    def test_missing_approval_manifest_fails_before_runtime(self):
        (self.files.root / "approved_manifest.json").unlink()
        with patch.object(loader, "version", side_effect=AssertionError("runtime accessed")):
            with self.assertRaisesRegex(loader.BundleError, "approved manifest"):
                loader.load_cpu_bundle(self.files.root, self.files.digest)

    def test_missing_manifest_joint(self):
        self.files.manifest["training_action_axes"].pop()
        self.files.approve()
        with self.assertRaisesRegex(loader.BundleError, "axis name"):
            self.files.inspect()

    def test_permuted_manifest(self):
        axes = self.files.manifest["training_action_axes"]
        axes[0], axes[1] = axes[1], axes[0]
        self.files.approve()
        with self.assertRaisesRegex(loader.BundleError, "axis name"):
            self.files.inspect()

    def test_axis_unit_and_relative_meaning(self):
        canonical = deepcopy(self.files.manifest["training_action_axes"])
        for field, value in (("unit", "radian"), ("meaning", "relative_delta")):
            with self.subTest(field=field):
                axes = deepcopy(canonical)
                axes[0][field] = value
                self.files.manifest["training_action_axes"] = axes
                self.files.approve()
                with self.assertRaisesRegex(loader.BundleError, "unit or absolute meaning"):
                    self.files.inspect()

    def test_manifest_cannot_hide_permuted_training_feature_names(self):
        info = self.files.document(loader.TRAIN_FEATURES)
        names = info["features"]["action"]["names"]
        names[0], names[1] = names[1], names[0]
        self.files.write_json(loader.TRAIN_FEATURES, info)
        self.files.approve()
        with self.assertRaisesRegex(loader.BundleError, "actual training action names/order"):
            self.files.inspect()

    def test_old_manifest_digest_rejects_replacement(self):
        approved = self.files.digest
        self.files.manifest["training_run_id"] = "replacement"
        self.files.approve()
        with self.assertRaisesRegex(loader.BundleError, "approved manifest SHA-256 mismatch"):
            loader.inspect_bundle(self.files.root, approved)

    def test_artifact_replacements_fail_before_runtime(self):
        for name in (loader.WEIGHTS, loader.PRE, loader.POST, loader.GOLDEN,
                     loader.TRAIN_FEATURES, f"checkpoint/{POST_STATS}", "tokenizer/tokenizer.json"):
            with self.subTest(artifact=name):
                path = self.files.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"tamper")
                try:
                    with self.assertRaisesRegex(loader.BundleError, "SHA-256 mismatch"):
                        self.files.inspect()
                finally:
                    path.write_bytes(original)

    def test_missing_weight_file(self):
        (self.files.root / loader.WEIGHTS).unlink()
        with self.assertRaisesRegex(loader.BundleError, "missing or linked artifact"):
            self.files.inspect()

    def test_unbound_processor_stats(self):
        self.files.approve(omit={f"checkpoint/{POST_STATS}"})
        with self.assertRaisesRegex(loader.BundleError, "unbound processor statistics"):
            self.files.inspect()

    def test_ambiguous_golden_is_rejected(self):
        golden = self.files.document(loader.GOLDEN)
        golden["expected_by_joint"][0][JOINTS[1]] = golden["expected_by_joint"][0][JOINTS[0]]
        self.files.write_json(loader.GOLDEN, golden)
        self.files.approve()
        with self.assertRaisesRegex(loader.BundleError, "golden axes must be distinguishable"):
            self.files.inspect()

    def test_bridge_swap_before_processor_fails_golden(self):
        class SwappedBridge(FixtureBridge):
            def postprocess(self, values):
                return super().postprocess(swapped(values))

        with self.assertRaisesRegex(loader.BundleError, "shoulder_pan"):
            loader.guarded_adapter(self.files.inspect(), SwappedBridge())

    def test_processor_output_swap_fails_golden(self):
        bridge = FixtureBridge()
        post = bridge.postprocess
        bridge.postprocess = lambda values: swapped(post(values))
        with self.assertRaisesRegex(loader.BundleError, "shoulder_pan"):
            loader.guarded_adapter(self.files.inspect(), bridge)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("torch", "lerobot", "safetensors")),
                     "isolated LeRobot CPU environment required")
class OfficialPostprocessorTests(unittest.TestCase):
    def setUp(self):

        self.enterContext(patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "-1", "HF_HUB_OFFLINE": "1"}))
        import torch
        from safetensors.torch import save_file

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.files = BundleFiles(Path(directory.name))
        self.torch = torch
        self.save_file = save_file
        self.write_stats(MEANS, STDS)

    def write_stats(self, means, stds):
        path = self.files.root / "checkpoint" / POST_STATS
        self.save_file(
            {"action.mean": self.torch.tensor(means, dtype=self.torch.float32, device="cpu"),
             "action.std": self.torch.tensor(stds, dtype=self.torch.float32, device="cpu")},
            path,
        )
        self.files.approve()

    def make_bridge(self):
        class ResetOnly:
            def reset(self):
                pass

        post = loader.load_saved_postprocessor(self.files.root / "checkpoint")
        return loader.OfficialCPUBridge(self.torch, ResetOnly(), ResetOnly(), post)

    def test_official_cpu_config_overrides_training_device(self):
        info = self.files.document(loader.CONFIG)
        info.update(device="cuda", use_amp=True)
        self.files.write_json(loader.CONFIG, info)
        config = loader.load_cpu_config(self.files.root / "checkpoint")
        self.assertEqual(config.device, "cpu")
        self.assertFalse(config.use_amp)
        self.assertEqual(config.action_feature.shape, (6,))

    def test_official_postprocessor_unnormalizes_and_preserves_axes(self):
        loader.guarded_adapter(self.files.inspect(), self.make_bridge())

    def test_official_postprocessor_stats_permutation_fails_golden(self):
        self.write_stats((MEANS[1], MEANS[0], *MEANS[2:]), (STDS[1], STDS[0], *STDS[2:]))
        with self.assertRaisesRegex(loader.BundleError, "shoulder_pan"):
            loader.guarded_adapter(self.files.inspect(), self.make_bridge())

    def test_official_bridge_swap_fails_golden(self):
        bridge = self.make_bridge()
        post = bridge.postprocess
        bridge.postprocess = lambda chunk: post(chunk[..., [1, 0, 2, 3, 4, 5]])
        with self.assertRaisesRegex(loader.BundleError, "shoulder_pan"):
            loader.guarded_adapter(self.files.inspect(), bridge)


if __name__ == "__main__":
    unittest.main()
