import contextlib
import base64
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import act_dry
import direct_live
import entry


class ActAdmissionTests(unittest.TestCase):
    def dispatch(self, *args):
        with patch.object(sys, "argv", ["entry.py", *args]), patch.object(sys, "addaudithook"):
            return entry.main()

    def test_default_and_explicit_smolvla_reach_the_same_legacy_function(self):
        for options in ((), ("--policy", "smolvla")):
            with patch("startup_simulation.run_dry", return_value=0) as smol:
                self.assertEqual(self.dispatch("--dry-run", "--duration", "60", "--speed", ".5", *options), 0)
                smol.assert_called_once_with(60., .5)

    def test_act_dry_dispatch_and_invalid_live_requests(self):
        with patch("act_dry.run", return_value=0) as act:
            self.dispatch("--policy", "act", "--dry-run", "--act-checkpoint", "bundle", "--n-action-steps", "25")
            self.assertEqual(act.call_args.args[0].n_action_steps, 25)
            for flags in (("--live", "--execute", "--operator-present"), ("--dry-run", "--execute"),
                          ("--mock",), ("--dry-run", "--mode", "raw"), ("--dry-run", "--replay", "93")):
                with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.dispatch("--policy", "act", "--act-checkpoint", "bundle", *flags)
            act.assert_called_once()

    def test_chunk_length_is_bound_to_the_selected_policy(self):
        for n in (25, 50, 100):
            policy = object.__new__(act_dry.ActPolicy)
            policy.steps = n
            rows = [[0.] * 6 for _ in range(n)]
            self.assertEqual(policy.validate_chunk(rows), rows)
            with self.assertRaises(ValueError):
                policy.validate_chunk(rows[:-1])
            if n != 50:
                with self.assertRaises(direct_live.Stop):
                    direct_live.chunk(rows)
            rows[-1][-1] = float("nan")
            with self.assertRaises(direct_live.Stop):
                policy.validate_chunk(rows)

    def test_serial_paths_denied_before_open(self):
        for path in ("/dev/ttyUSB0", "/dev/ttyACM0", "/dev/serial/by-id/follower"):
            with self.assertRaises(RuntimeError):
                act_dry.deny_motor_port("open", (path, "rb"))

    def test_preview_requires_fresh_distinct_three_view_camera_only_publications(self):
        from PIL import Image

        data = io.BytesIO()
        Image.new("RGB", (640, 480)).save(data, "JPEG")
        images = dict.fromkeys(act_dry.VIEWS, base64.b64encode(data.getvalue()).decode())
        with tempfile.TemporaryDirectory() as tmp:
            source = act_dry.AlignmentViews(Path(tmp))
            source.path = Path(tmp) / "frames.json"
            state = dict(phase="ready", captured=time.time(), motor_access=False, register_writes=0, images=images)
            source.path.write_text(json.dumps(state))
            self.assertEqual(set(source.fresh_frames()), set(act_dry.VIEWS))
            self.assertIsNone(source.fresh_frames())
            for change in ({"captured": time.time()-2}, {"phase": "closed"}, {"images": {}}):
                source.path.write_text(json.dumps({**state, "captured": time.time(), **change}))
                self.assertIsNone(source.fresh_frames())
            source.path.write_text(json.dumps({**state, "motor_access": True}))
            with self.assertRaises(ValueError):
                source.camera_frames()
