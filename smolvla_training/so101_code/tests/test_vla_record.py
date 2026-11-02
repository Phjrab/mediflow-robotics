import builtins
import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vla_record as rec
SNAPSHOT_READER = rec.read_snapshot


class FakeDataset:
    """Real temporary files with injected parquet row reader; no robot or media backend."""
    def __init__(self, root):
        self.root = Path(root)
        self.tables = {}
        self.meta = []
        self.frames = []
        self.tasks = []

    def put(self, path, rows=None, data=b"mock-payload"):
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        if rows is not None:
            self.tables[str(full.resolve())] = copy.deepcopy(rows)

    def append(self, task):
        index, start = len(self.meta), len(self.frames)
        if task not in self.tasks:
            self.tasks.append(task)
        task_index = self.tasks.index(task)
        meta = {"episode_index": index, "length": 2, "tasks": [task],
                "dataset_from_index": start, "dataset_to_index": start + 2,
                "data/chunk_index": 0, "data/file_index": 0}
        for role in rec.ROLES:
            key = "observation.images." + role
            for suffix, value in (("chunk_index", 0), ("file_index", index), ("from_timestamp", 0), ("to_timestamp", 2/30)):
                meta[f"videos/{key}/{suffix}"] = value
            self.put(f"videos/{key}/chunk-000/file-{index:03d}.mp4")
        self.meta.append(meta)
        for fi in range(2):
            self.frames.append({"index": start + fi, "episode_index": index, "frame_index": fi,
                                "timestamp": fi/30, "task_index": task_index,
                                "action": [1.0]*6, "observation.state": [0.0]*6})
        info = {"codebase_version": "v3.0", "total_episodes": len(self.meta), "total_frames": len(self.frames),
                "features": {"action": {"shape": [6]}, "observation.state": {"shape": [6]},
                             **{f"observation.images.{r}": {} for r in rec.ROLES}},
                "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
                "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"}
        self.put("meta/info.json", data=json.dumps(info).encode())
        self.put("meta/tasks.parquet", [{"task_index": i, "task": t} for i, t in enumerate(self.tasks)])
        self.put("meta/episodes/chunk-000/file-000.parquet", self.meta)
        self.put("data/chunk-000/file-000.parquet", self.frames)

    def snapshot(self, root=None):
        return SNAPSHOT_READER(root or self.root, rows=lambda p: copy.deepcopy(self.tables[str(p)]))


class TasksTests(unittest.TestCase):
    def setUp(self):
        self.cfg = rec.load_tasks(ROOT / "configs/vla_tasks.yaml")
        self.counts = dict.fromkeys(rec.COMBOS, 0)

    def test_nine_combos_and_27_sentences(self):
        self.assertEqual(len(self.cfg["combinations"]), 9)
        self.assertEqual(sum(len(e["ko"])+len(e["en"]) for e in self.cfg["combinations"].values()), 27)

    def test_minimum_count_and_deterministic_tie(self):
        self.counts["A:red"] = 5
        self.assertEqual(rec.choose_combo(self.counts, True), "A:blue")
        self.counts["A:blue"] = 1
        self.assertEqual(rec.choose_combo(self.counts, True), "A:green")

    def test_single_pipeline_gate(self):
        self.counts["A:red"] = 12
        self.assertEqual(rec.choose_combo(self.counts), "A:red")
        with self.assertRaises(rec.RecordError):
            rec.choose_combo(self.counts, manual="C:green")

    def test_manual_with_expansion(self):
        self.assertEqual(rec.choose_combo(self.counts, True, "B:blue"), "B:blue")

    def test_cycle_korean(self):
        entry = self.cfg["combinations"]["A:red"]
        self.assertEqual([rec.choose_sentence(self.cfg, "A:red", n) for n in range(3)], entry["ko"] + entry["ko"][:1])

    def test_cycle_mixed(self):
        self.cfg["language_mode"] = "mixed"
        self.assertEqual(rec.choose_sentence(self.cfg, "A:red", 2), self.cfg["combinations"]["A:red"]["en"][0])

    def test_random_seed_reproducible(self):
        self.cfg["sentence_selection"] = "random"
        first = [rec.choose_sentence(self.cfg, "B:green", i) for i in range(30)]
        self.assertEqual(first, [rec.choose_sentence(self.cfg, "B:green", i) for i in range(30)])
        self.cfg["random_seed"] += 1
        self.assertNotEqual(first, [rec.choose_sentence(self.cfg, "B:green", i) for i in range(30)])
        self.assertTrue(set(first) <= set(self.cfg["combinations"]["B:green"]["ko"]))

    def test_imbalance_boundary(self):
        self.counts["A:red"] = 3
        self.assertFalse(rec.imbalance(self.counts, 3)["warning"])
        self.counts["A:red"] = 4
        self.assertTrue(rec.imbalance(self.counts, 3)["warning"])

    def test_blank_duplicate_or_missing_combo_rejected(self):
        for change in ("blank", "duplicate", "missing", "target"):
            cfg = copy.deepcopy(self.cfg)
            if change == "blank":
                cfg["combinations"]["A:red"]["ko"][0] = " "
            elif change == "duplicate":
                cfg["combinations"]["B:red"]["en"] = cfg["combinations"]["A:red"]["en"]
            elif change == "missing":
                del cfg["combinations"]["A:red"]
            else:
                cfg["targets"]["secondary_per_combo"] = 31
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "tasks.yaml"
                path.write_text(rec.yaml.safe_dump(cfg), encoding="utf-8")
                with self.subTest(change=change), self.assertRaises(rec.RecordError):
                    rec.load_tasks(path)

    def test_command_task_camera_keys_append_and_no_upload(self):
        cameras = rec.load_cameras(ROOT / "configs/vla_cameras.yaml")
        task = rec.choose_sentence(self.cfg, "C:blue", 0)
        command = rec.build_command("local/test", Path("/tmp/new-test"), task, cameras, True)
        self.assertIn("--dataset.single_task="+task, command)
        self.assertIn("--dataset.num_episodes=1", command)
        self.assertIn("--resume=true", command)
        self.assertIn("--dataset.push_to_hub=false", command)
        cameras_arg = next(x for x in command if x.startswith("--robot.cameras=")).split("=", 1)[1]
        self.assertEqual(set(json.loads(cameras_arg)), set(rec.ROLES))
        self.assertFalse(any("rename_map" in x for x in command))


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "dataset"
        self.counter = Path(self.tmp.name) / "counter.json"
        self.fixture = FakeDataset(self.root)
        self.before = self.fixture.snapshot()
        self.task = rec.load_tasks(ROOT / "configs/vla_tasks.yaml")["combinations"]["A:red"]["ko"][0]

    def commit(self, **kwargs):
        args = dict(exit_code=0, completed=True, interrupted=False, snapshot_reader=self.fixture.snapshot,
                    identity_reader=lambda _: "local/test_20261028_120000")
        args.update(kwargs)
        return rec.commit_saved_episode(self.counter, self.root, "local/test", self.before, 0, "A:red", self.task, **args)

    def test_positive_actual_metadata_and_required_files(self):
        self.fixture.append(self.task)
        saved = self.commit()
        self.assertEqual(saved["counts"]["A:red"], 1)
        self.assertEqual(rec.load_counter(self.counter, self.root.resolve(), "local/test"), saved)
        self.assertFalse(self.counter.with_suffix(".json.lock").exists())

    def test_duplicate_commit_or_stale_revision_rejected(self):
        self.fixture.append(self.task)
        self.commit()
        previous = self.counter.read_bytes()
        with self.assertRaises(rec.RecordError):
            self.commit()
        self.assertEqual(self.counter.read_bytes(), previous)

    def test_two_appends_same_dataset(self):
        self.fixture.append(self.task)
        self.commit()
        before = self.fixture.snapshot()
        self.fixture.append(self.task)
        saved = rec.commit_saved_episode(self.counter, self.root, "local/test", before, 1, "A:red", self.task,
                                          exit_code=0, completed=True, interrupted=False, snapshot_reader=self.fixture.snapshot,
                                          identity_reader=lambda _: "local/test_20261028_120000")
        self.assertEqual(saved["counts"]["A:red"], 2)
        self.assertEqual(saved["actual_repo_id"], "local/test_20261028_120000")

    def test_missing_identity_provider_no_increment(self):
        self.fixture.append(self.task)
        with self.assertRaises(rec.RecordError):
            self.commit(identity_reader=None)
        self.assertFalse(self.counter.exists())

    def test_changed_actual_id_on_resume_rejected(self):
        self.fixture.append(self.task)
        self.commit()
        before = self.fixture.snapshot()
        previous_counter = self.counter.read_bytes()
        self.fixture.append(self.task)
        with self.assertRaises(rec.RecordError):
            rec.commit_saved_episode(self.counter, self.root, "local/test", before, 1, "A:red", self.task,
                                      exit_code=0, completed=True, interrupted=False, snapshot_reader=self.fixture.snapshot,
                                      identity_reader=lambda _: "local/test_20261028_120001")
        self.assertEqual(self.counter.read_bytes(), previous_counter)

    def test_requested_repo_or_root_binding_changed_rejected(self):
        self.fixture.append(self.task)
        self.commit()
        for root, repo in ((self.root, "local/other"), (self.root.parent / "other", "local/test")):
            with self.assertRaises(rec.RecordError):
                rec.load_counter(self.counter, root, repo)

    def test_resume_plan_uses_recorded_actual_id(self):
        self.fixture.append(self.task)
        self.commit()
        with patch.object(rec, "read_snapshot", side_effect=self.fixture.snapshot), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(rec.main(["--repo-id", "local/test", "--dataset-root", str(self.root), "--counter", str(self.counter)]), 0)
            command = json.loads(out.getvalue())["command_draft"]
        self.assertIn("--dataset.repo_id=local/test_20261028_120000", command)
        self.assertIn("--resume=true", command)
        self.assertIn("--dataset.root="+str(self.root), command)

    def test_simultaneous_commits_only_one_increment(self):
        self.fixture.append(self.task)
        def attempt(_):
            try:
                self.commit()
                return True
            except rec.RecordError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(rec.load_counter(self.counter, self.root, "local/test")["counts"]["A:red"], 1)

    def test_exit_zero_without_growth_rejected(self):
        with self.assertRaises(rec.RecordError):
            self.commit()
        self.assertFalse(self.counter.exists())

    def test_failure_interruption_partial_with_growth_rejected(self):
        self.fixture.append(self.task)
        for opts in ({"exit_code": 1}, {"interrupted": True}, {"completed": False}, {"completed": None}):
            with self.subTest(opts=opts), self.assertRaises(rec.RecordError):
                self.commit(**opts)
            self.assertFalse(self.counter.exists())

    def test_two_new_episodes_rejected(self):
        self.fixture.append(self.task)
        self.fixture.append(self.task)
        with self.assertRaises(rec.RecordError):
            self.commit()

    def test_wrong_task_rejected(self):
        self.fixture.append("wrong task")
        with self.assertRaises(rec.RecordError):
            self.commit()

    def test_corrupt_counter_preserved(self):
        for content in ("{bad", '{"schema_version":1,"schema_version":1}', 'null'):
            self.counter.write_text(content)
            with self.assertRaises((ValueError, rec.RecordError)):
                self.commit()
            self.assertEqual(self.counter.read_text(), content)

    def test_atomic_replace_failure_no_increment(self):
        self.fixture.append(self.task)
        old = rec.new_counter(self.root.resolve(), "local/test")
        self.counter.write_text(json.dumps(old))
        original = self.counter.read_bytes()
        with patch.object(rec.os, "replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.commit()
        self.assertEqual(self.counter.read_bytes(), original)
        self.assertFalse(list(self.counter.parent.glob("counter.json.*")))

    def test_atomic_fsync_failure_no_file(self):
        self.fixture.append(self.task)
        with patch.object(rec.os, "fsync", side_effect=OSError("fsync failure")), self.assertRaises(OSError):
            self.commit()
        self.assertFalse(self.counter.exists())

    def test_out_of_band_dataset_change_before_replace_rejected(self):
        self.fixture.append(self.task)
        after = self.fixture.snapshot()
        altered = rec.Snapshot(after.root, after.frames + 1, after.episodes)
        with patch.object(rec, "read_snapshot", side_effect=[after, altered]):
            with self.assertRaises(rec.RecordError):
                self.commit(snapshot_reader=rec.read_snapshot)
        self.assertFalse(self.counter.exists())

    def test_combo_task_mismatch_rejected(self):
        with self.assertRaises(rec.RecordError):
            rec.commit_saved_episode(self.counter, self.root, "local/test", self.before, 0, "C:green", self.task,
                                      exit_code=0, completed=True, interrupted=False)
        self.assertFalse(self.counter.exists())

    def test_concurrent_or_stale_lock_not_stolen(self):
        lock = self.counter.with_suffix(".json.lock")
        lock.write_text("owned")
        with self.assertRaises(rec.RecordError):
            self.commit()
        self.assertEqual(lock.read_text(), "owned")

    def test_overlapping_and_symlink_paths(self):
        for path in (self.root, self.root / "counter.json", self.root.parent):
            with self.assertRaises(rec.RecordError):
                rec.separate_paths(self.root, path)
        self.counter.symlink_to(self.root / "inside.json")
        with self.assertRaises(rec.RecordError):
            self.commit()

    def test_missing_video_rejected(self):
        self.fixture.append(self.task)
        next((self.root / "videos").rglob("*.mp4")).unlink()
        with self.assertRaises(rec.RecordError):
            self.commit()

    def test_metadata_wrong_count_and_timestamp(self):
        self.fixture.append(self.task)
        path = self.root / "meta/info.json"
        info = json.loads(path.read_text())
        info["total_episodes"] = 2
        path.write_text(json.dumps(info))
        with self.assertRaises(rec.RecordError):
            self.fixture.snapshot()
        info["total_episodes"] = 1
        path.write_text(json.dumps(info))
        data = str((self.root / "data/chunk-000/file-000.parquet").resolve())
        self.fixture.tables[data][1]["timestamp"] = 0
        with self.assertRaises(rec.RecordError):
            self.fixture.snapshot()

    def test_changed_old_episode_rejected(self):
        self.fixture.append(self.task)
        self.commit()
        before = self.fixture.snapshot()
        self.fixture.append(self.task)
        data = str((self.root / "data/chunk-000/file-000.parquet").resolve())
        self.fixture.tables[data][0]["action"][0] = 50
        with self.assertRaises(rec.RecordError):
            rec.confirm_save(before, self.fixture.snapshot(), self.task, 0, True, False)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.cameras = rec.load_cameras(ROOT / "configs/vla_cameras.yaml")
        for role, cfg in self.cameras["cameras"].items():
            cfg["device"] = "/dev/v4l/by-id/usb-" + role
        self.evidence = {"observed_at_monotonic": time.monotonic(), "camera_stage1_pass": True,
                         "canonical_ports_distinct": True, "exclusive_ownership": True,
                         "temperatures_c": [35]*6, "pipeline_a_red_verified": True}

    def reasons(self, evidence=None, **kwargs):
        evidence = evidence or self.evidence
        class Provider:
            def observe(self):
                return evidence
        return rec.preflight(self.cameras, provider=Provider(), **kwargs)

    def test_all_green_mock_still_cannot_launch_raw_recorder(self):
        self.assertEqual(self.reasons(first_motion_approved=True, expanded=True), [rec.RUNTIME_BLOCK])

    def test_first_motion_gate(self):
        self.assertIn("first motion approval missing", self.reasons())

    def test_camera_port_ownership_failures(self):
        for key in ("camera_stage1_pass", "canonical_ports_distinct", "exclusive_ownership"):
            evidence = {**self.evidence, key: None}
            self.assertIn(key + " unverified", self.reasons(evidence))

    def test_temperature_boundary_missing_nan(self):
        for temps in (None, [], [49]*5, [50]*6, [float("nan")]*6, [-1]*6):
            self.assertTrue(any("temperatures" in s for s in self.reasons({**self.evidence, "temperatures_c": temps})))
        self.assertFalse(any("temperatures" in s for s in self.reasons({**self.evidence, "temperatures_c": [49.99]*6})))

    def test_stale_or_future_evidence(self):
        for timestamp in (time.monotonic()-3, time.monotonic()+10, float("nan")):
            self.assertIn("stale preflight observation", self.reasons({**self.evidence, "observed_at_monotonic": timestamp}))

    def test_expand_needs_pipeline_verification(self):
        reasons = self.reasons({**self.evidence, "pipeline_a_red_verified": False}, expanded=True)
        self.assertTrue(any("A:red real pipeline" in s for s in reasons))

    def test_unavailable_provider(self):
        class Provider:
            def observe(self):
                raise OSError()
        self.assertIn("read-only live evidence unavailable", rec.preflight(self.cameras, provider=Provider()))


class CLITests(unittest.TestCase):
    def test_dryrun_and_execute_have_no_device_subprocess_or_write(self):
        original_import, original_open = builtins.__import__, builtins.open
        def guard_import(name, *args, **kwargs):
            if name.startswith(("serial", "lerobot", "cv2", "medicine_sorter", "scservo")):
                raise AssertionError("forbidden import " + name)
            return original_import(name, *args, **kwargs)
        def guard_open(file, mode="r", *args, **kwargs):
            if str(file).startswith("/dev/") or any(s in mode for s in "wax+"):
                raise AssertionError("unexpected write/device open")
            return original_open(file, mode, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            base = ["--dataset-root", directory+"/new-data", "--counter", directory+"/new-counter.json"]
            for flags, exit_code in (([], 0), (["--expand-nine", "--combo", "C:blue"], 0), (["--execute", "--first-motion-approved"], 2)):
                with patch("builtins.__import__", side_effect=guard_import), patch("builtins.open", side_effect=guard_open), patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")), patch("os.open", side_effect=AssertionError("os.open forbidden")), patch("sys.stdout", new_callable=io.StringIO) as out:
                    self.assertEqual(rec.main(base + flags), exit_code)
                    self.assertFalse(json.loads(out.getvalue())["command_is_executable"])
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_no_json_evidence_cli(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            rec.main(["--approval-json", "fake.json"])

    def test_counter_mismatch_no_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            counter = Path(directory) / "counter.json"
            counter.write_text("bad-json")
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(rec.main(["--counter", str(counter), "--dataset-root", directory+"/data"]), 2)
            self.assertEqual(counter.read_text(), "bad-json")


if __name__ == "__main__":
    unittest.main()
