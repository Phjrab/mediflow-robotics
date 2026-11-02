"""Hardware-free tests: fake OpenCV, fake metadata and child-process channels."""
import builtins
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vla_check", ROOT / "scripts/check_vla_cameras.py")
cam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cam)


def config():
    return {"schema_version": 1, "cameras": {
        role: {"device": f"/dev/v4l/by-id/usb-{role}-video-index0", "width": 8, "height": 6, "fps": 30}
        for role in cam.ROLES}}


def resolve(path):
    index = next(i for i, role in enumerate(cam.ROLES) if role in path)
    return {"target": f"/dev/video{index}", "rdev": index, "physical": f"usb-{index}"}


class VLAConfigTests(unittest.TestCase):
    def test_template_valid_but_not_connected(self):
        report, code = cam.check(cam.load_config(cam.DEFAULT_CONFIG))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertFalse(report["hardware_verified"])
        self.assertTrue(all(not x["identity_configured"] for x in report["cameras"].values()))

    def test_valid_config(self):
        self.assertEqual(cam.validate_config(config()), config())

    def test_missing_extra_wrong_keys(self):
        for wrong in ({}, {"wrist": {}}, {**config()["cameras"], "ceiling": {}}):
            with self.subTest(wrong=wrong), self.assertRaises(cam.ConfigError):
                cam.validate_config({"schema_version": 1, "cameras": wrong})

    def test_duplicate_yaml_keys(self):
        for text in ("cameras: {}\ncameras: {}", "cameras:\n  wrist: {}\n  wrist: {}",
                     "base: &base {width: 8}\nchild: {<<: *base, width: 9}"):
            with self.subTest(text=text), self.assertRaises(cam.ConfigError):
                cam.yaml.load(text, Loader=cam.UniqueLoader)

    def test_yaml_unsafe_object_rejected(self):
        with self.assertRaises(cam.yaml.YAMLError):
            cam.yaml.load("!!python/object/apply:os.system ['false']", Loader=cam.UniqueLoader)

    def test_bad_roots_and_version(self):
        for data in (None, [], {**config(), "schema_version": True}, {**config(), "schema_version": 2}):
            with self.subTest(data=data), self.assertRaises(cam.ConfigError):
                cam.validate_config(data)

    def test_numeric_tty_urls_traversal_rejected(self):
        for device in (0, True, "0", "/dev/video4", "/dev/ttyACM0", "http://camera/", "/dev/v4l/by-id/../ttyACM0", "/dev/v4l/by-id/.."):
            data = config()
            data["cameras"]["wrist"]["device"] = device
            with self.subTest(device=device), self.assertRaises(cam.ConfigError):
                cam.validate_config(data)

    def test_bad_dimensions_and_fps(self):
        for key in ("width", "height", "fps"):
            for value in (0, -1, True, 1.5, "30", 10000):
                data = config()
                data["cameras"]["top"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(cam.ConfigError):
                    cam.validate_config(data)

    def test_duplicate_paths_and_interfaces(self):
        for suffix in ("0", "1"):
            data = config()
            data["cameras"]["top"]["device"] = "/dev/v4l/by-id/usb-wrist-video-index" + suffix
            with self.assertRaises(cam.ConfigError):
                cam.validate_config(data)

    def test_alias_to_serial_or_regular_file_rejected(self):
        with patch.object(cam.Path, "is_symlink", return_value=True):
            with patch.object(cam.Path, "resolve", return_value=Path("/dev/ttyACM0")):
                with self.assertRaises(cam.ConfigError):
                    cam.resolve_device(config()["cameras"]["wrist"]["device"])
            with patch.object(cam.Path, "resolve", return_value=Path("/dev/video0")), patch.object(cam.Path, "stat", return_value=MagicMock(st_mode=0o100644)):
                with self.assertRaises(cam.ConfigError):
                    cam.resolve_device(config()["cameras"]["wrist"]["device"])

    def test_unresolved_or_duplicate_metadata_opens_nothing(self):
        data = config()
        null_data = copy.deepcopy(data)
        null_data["cameras"]["oblique"]["device"] = None
        for cfg, resolver in ((null_data, resolve), (data, lambda _: resolve("wrist")),
                              (data, lambda p: {**resolve(p), "physical": "same-usb"})):
            runner = MagicMock()
            report, code = cam.check(cfg, capture=True, resolver=resolver, runner=runner)
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "NOT_RUN")
            runner.assert_not_called()

    def test_validate_never_resolves_or_captures(self):
        resolver, runner = MagicMock(), MagicMock()
        cam.check(config(), resolver=resolver, runner=runner)
        resolver.assert_not_called()
        runner.assert_not_called()


class VLACaptureTests(unittest.TestCase):
    def fake(self, frame=None):
        cv = MagicMock()
        cap = cv.VideoCapture.return_value
        cap.isOpened.return_value = True
        cap.get.return_value = 30
        cap.read.return_value = (True, frame if frame is not None else np.full((6, 8, 3), 100, np.uint8))
        return cv, cap

    def test_capture_metrics_and_release(self):
        cv, cap = self.fake()
        cfg = config()["cameras"]["wrist"]
        result = cam.capture_one(cfg, cv)
        cv.VideoCapture.assert_called_once_with(cfg["device"], cv.CAP_V4L2)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["configured_fps"], 30)
        self.assertEqual(result["backend_reported_fps"], 30)
        self.assertIsNone(result["measured_fps"])
        self.assertIsNone(result["sensor_capture_timestamp"])
        self.assertFalse(result["liveness_verified"])
        self.assertIn("+00:00", result["host_received_at_utc"])
        cap.release.assert_called_once()

    def test_black_and_wrong_size_fail(self):
        for frame in (np.zeros((6, 8, 3), np.uint8), np.full((5, 8, 3), 100, np.uint8)):
            cv, cap = self.fake(frame)
            self.assertEqual(cam.capture_one(config()["cameras"]["wrist"], cv)["status"], "FAIL")
            cap.release.assert_called_once()

    def test_read_and_open_failure_release(self):
        for stage in ("open", "read"):
            cv, cap = self.fake()
            if stage == "open":
                cap.isOpened.return_value = False
            else:
                cap.read.return_value = (False, None)
            with self.assertRaises(RuntimeError):
                cam.capture_one(config()["cameras"]["wrist"], cv)
            cap.release.assert_called_once()

    def test_wrong_dtype_or_channels(self):
        for frame in (np.ones((6, 8), np.uint8), np.ones((6, 8, 3), np.float32)):
            cv, cap = self.fake(frame)
            with self.assertRaises(RuntimeError):
                cam.capture_one(config()["cameras"]["wrist"], cv)
            cap.release.assert_called_once()

    def test_nonfinite_fps_not_reported(self):
        for fps in (float("nan"), float("inf"), 0, -1):
            self.assertIsNone(cam.positive_finite(fps))

    def test_capture_aggregates_failure(self):
        runner = MagicMock(side_effect=[{"status": "PASS"}, {"status": "FAIL"}, {"status": "PASS"}])
        report, code = cam.check(config(), True, resolver=resolve, runner=runner)
        self.assertEqual(code, 1)
        self.assertFalse(report["hardware_verified"])
        self.assertEqual(runner.call_count, 3)

    def test_child_timeout_bounded_and_reaped(self):
        ctx, reader, writer, process = (MagicMock() for _ in range(4))
        ctx.Pipe.return_value = (reader, writer)
        ctx.Process.return_value = process
        reader.poll.return_value = False
        process.is_alive.side_effect = [True, True]
        result = cam.bounded_capture({}, {}, 0.1, context=ctx)
        self.assertEqual(result["status"], "FAIL")
        reader.poll.assert_called_once_with(0.1)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        reader.close.assert_called_once()
        writer.close.assert_called_once()

    def test_worker_rejects_hotplug_before_backend_import(self):
        connection = MagicMock()
        with patch.object(cam, "resolve_device", return_value={"changed": True}):
            cam.capture_worker(connection, config()["cameras"]["wrist"], {})
        self.assertEqual(connection.send.call_args.args[0]["status"], "FAIL")
        connection.close.assert_called_once()

    def test_child_start_failure_closes_pipe(self):
        ctx, reader, writer = (MagicMock() for _ in range(3))
        ctx.Pipe.return_value = (reader, writer)
        ctx.Process.return_value.start.side_effect = OSError("spawn failed")
        result = cam.bounded_capture({}, {}, 0.1, context=ctx)
        self.assertEqual(result["status"], "FAIL")
        reader.close.assert_called_once()
        writer.close.assert_called_once()

    def test_no_robot_or_serial_imports_in_mock_flow(self):
        original = builtins.__import__
        forbidden = ("serial", "lerobot", "medicine_sorter", "feetech", "scservo")
        def guarded(name, *args, **kwargs):
            if name.startswith(forbidden):
                raise AssertionError(f"forbidden import: {name}")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            spec.loader.exec_module(cam)
            cv, _ = self.fake()
            result, code = cam.check(config(), True, resolver=resolve,
                                     runner=lambda cfg, *_: cam.capture_one(cfg, cv))
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "PASS")


class VLACLITests(unittest.TestCase):
    def test_default_validate_json(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cam.main([]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "NOT_RUN")

    def test_template_capture_not_run(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cam.main(["--capture"]), 2)
            self.assertFalse(json.loads(output.getvalue())["hardware_verified"])

    def test_existing_output_preserved_before_device_check(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.touch()
            with patch.object(cam, "check") as check, patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(cam.main(["--capture", "--output", str(output)]), 2)
                check.assert_not_called()
            self.assertEqual(output.read_bytes(), b"")

    def test_bad_config_still_reports_json(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cam.main(["--config", "/nonexistent-vla-config.yaml"]), 2)
            self.assertFalse(json.loads(output.getvalue())["schema_valid"])

    def test_invalid_timeout(self):
        for timeout in ("nan", "inf", "0", "-1", "31"):
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                cam.main(["--timeout-seconds", timeout])


if __name__ == "__main__":
    unittest.main()
