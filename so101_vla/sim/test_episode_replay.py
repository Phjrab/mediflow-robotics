"""CPU-only verified episode replay fixtures."""
import hashlib
import io as textio
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import episode_replay as ep
from startup_simulation import SimulatedArm

CONFIG = ep.load()


class EpisodeReplayTests(unittest.TestCase):
    def recording(self, root, actions):
        private = root / "private"
        private.mkdir()
        record = {"schema_version":1, "fps":30, "joints":list(ep.base.JOINTS),
                  "calibration_sha256":CONFIG["calibration_sha256"], "task":"Put medicine bottle A into the blue basket",
                  "episode":93, "source":"blue_source", "source_episode":1, "actions":actions}
        path = private / "ep_093.json"
        document = json.dumps(record).encode()
        path.write_bytes(document)
        manifest = {key:record[key] for key in ("schema_version","fps","joints","calibration_sha256")}
        manifest["episodes"] = {"93":{"file":"private/ep_093.json", "sha256":hashlib.sha256(document).hexdigest(),
                                      "color":"blue", "episode":93, "source":"blue_source", "source_episode":1,
                                      "revision":"test-revision", "frames":len(actions)}}
        manifest_path = root/"replay_manifest.json"
        manifest_path.write_text(json.dumps(manifest),encoding="utf-8")
        reference = root/"grasp_motion_reference.json"
        reference.write_text(json.dumps({"replay_manifest_sha256":hashlib.sha256(manifest_path.read_bytes()).hexdigest()}),
                             encoding="utf-8")
        return manifest_path, path, reference

    def arm(self, logs, case="normal"):
        return SimulatedArm(CONFIG["home"]["state"], {}, logs.append, case)

    def test_selected_recording_hash_contract_and_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            actions = [CONFIG["home"]["state"][:]]
            manifest, path, reference = self.recording(Path(root),actions)
            with patch.object(ep,"PUBLIC_REFERENCE",reference):
                recording, provenance = ep.load_episode(manifest,93,calibration_sha256=CONFIG["calibration_sha256"])
                self.assertEqual(recording["actions"],actions)
                self.assertEqual(provenance["episode"],93)
                path.write_bytes(path.read_bytes()+b" ")
                with self.assertRaisesRegex(ValueError,"action file SHA-256"):
                    ep.load_episode(manifest,93,calibration_sha256=CONFIG["calibration_sha256"])
                manifest.write_bytes(manifest.read_bytes()+b" ")
                with self.assertRaisesRegex(ValueError,"manifest SHA-256"):
                    ep.load_episode(manifest,93,calibration_sha256=CONFIG["calibration_sha256"])

    def test_refuses_unsupported_speed_duration_id_and_live_without_operator(self):
        for speed,duration in ((1,10),(.33,0),(.5,181),(.5,float("nan"))):
            with self.subTest(speed=speed,duration=duration),self.assertRaises(ValueError):
                ep.validate_options(speed,duration)
        with self.assertRaises(ValueError):
            ep.load_episode("missing",137,calibration_sha256="")
        with patch.dict(os.environ,{"SO101_DIRECT_OPERATOR":"0"}),self.assertRaisesRegex(ValueError,"authorization"):
            ep.run_episode(93,180,.5,dry_run=False)

    def test_original_order_at_both_speeds_with_shared_clipping(self):
        actions = [CONFIG["home"]["state"][:4] + [float(i), grip] for i,grip in enumerate([4,55,36,22])]
        actions[1][2] = 170.
        for speed in ep.ALLOWED_SPEEDS:
            with self.subTest(speed=speed):
                rows=[]
                arm=self.arm(rows)
                result=ep.replay(arm,actions,rows.append,speed=speed,duration=5)
                emitted=[x for x in rows if x["event"]=="recorded_goal_transmitted"]
                self.assertEqual(result["status"],"RUN_COMPLETE")
                self.assertEqual(result["replay_end"],"source_exhausted")
                self.assertEqual([row["source_frame"] for row in emitted],list(range(len(actions))))
                self.assertEqual([row["source_action"] for row in emitted],actions)
                self.assertEqual(result["gripper_opening_protection"]["policy_open_cap_clips"],1)
                self.assertLessEqual(emitted[1]["wire_normalized"][-1],48.5)
                self.assertEqual(emitted[1]["wire_raw"]["elbow_flex"],CONFIG["limits"]["elbow_flex"][1])
                for first,last in zip(emitted,emitted[1:]):
                    self.assertAlmostEqual(last["time_s"]-first["time_s"],1/(30*speed),places=6)
                self.assertEqual(arm.start_writes,[])

    def test_slow_checks_remain_ordered_and_stop_before_late_target(self):
        rows=[]
        arm=self.arm(rows)
        original=arm.checked

        def slow_checked():
            if arm.policy_active:
                arm.pause(.11)
            return original()

        arm.checked=slow_checked
        result=ep.replay(arm,[CONFIG["home"]["state"][:]]*25,rows.append,speed=.5,duration=.6)
        emitted=[x for x in rows if x["event"]=="recorded_goal_transmitted"]
        self.assertEqual(result["replay_end"],"duration_limit")
        self.assertGreater(len(emitted),1)
        self.assertEqual([row["source_frame"] for row in emitted],list(range(len(emitted))))
        self.assertTrue(all(row["time_s"]<.6 for row in emitted))
        self.assertTrue(all(b["time_s"]-a["time_s"]>=1/15 for a,b in zip(emitted,emitted[1:])))
        self.assertNotEqual(result["hold"]["status"],"NOT_NEEDED")

    def test_temperature_hold_remains_and_torque_off_start_uses_shared_seed(self):
        action=CONFIG["home"]["state"][:]
        rows=[]
        arm=self.arm(rows,"overheat")
        result=ep.replay(arm,[action]*120,rows.append,speed=.5,duration=20)
        self.assertEqual(result["status"],"HOLD")
        self.assertIn("온도 중단",result["reason"])
        self.assertGreater(result["steps"],20)
        arm=self.arm([],"all_off")
        result=ep.replay(arm,[action],lambda _:None,speed=.5,duration=20)
        self.assertEqual(result["status"],"RUN_COMPLETE")
        self.assertEqual(result["steps"],1)
        self.assertEqual([name for name,_ in arm.start_writes], ["Goal_Position","Torque_Enable"])
        self.assertTrue(all(arm.torque.values()))

    def test_pan_correction_separate_from_recorded_rows_with_existing_grasp_gate(self):
        rows=[]
        arm=self.arm(rows)
        unmodified = [[-4.,-4.,0.,40.,-2.,40. if i<20 else 20.] for i in range(28)]
        offset = ep.GraspOffset(CONFIG["lateral_correction"], height=lambda _:130.)
        with patch.object(ep,"GraspOffset",return_value=offset):
            result=ep.replay(arm,unmodified,rows.append,speed=.5,duration=10)
        self.assertEqual(result["status"],"RUN_COMPLETE")
        goals=[row for row in rows if row["event"]=="recorded_goal_transmitted"]
        self.assertEqual([row["source_action"] for row in goals],unmodified)
        self.assertFalse(result["policy_arrival_gate_enabled"])
        corrected=[row for row in goals if row["lateral_correction"]["applied_deg"]]
        self.assertTrue(corrected)
        for row in corrected:
            self.assertAlmostEqual(row["protected_action"][0]-row["source_action"][0],row["lateral_correction"]["applied_deg"])
            self.assertGreater(row["lateral_correction"]["applied_deg"],0)

    def test_camera_dry_dispatch_uses_simulated_arm_and_writes_replay_summary(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            manifest,_,reference=self.recording(root,[CONFIG["home"]["state"][:]]*2)
            camera=Mock()
            camera.camera_frames.return_value={}
            roles = dict.fromkeys(CONFIG["cameras"], object())
            camera.fresh_frames.side_effect=[None, {"end_effector": object()}, roles]
            camera.dark_cameras=set()
            camera.cleanup_errors=[]
            camera.bus=None
            camera.cameras_stats.summary.return_value={}
            recorder=Mock()
            recorder.goal.return_value=None
            recorder.summary.return_value={}
            monitor=Mock()
            monitor.hold_requested.return_value=False

            def virtual_arm(home, frames, log, case, *, real_time):
                self.assertTrue(real_time)
                return SimulatedArm(home,frames,log,"all_off")

            with patch.object(ep,"ROOT",root), patch.object(ep,"PUBLIC_REFERENCE",reference), \
                 patch.object(ep.sys,"addaudithook") as guard, \
                 patch("startup_simulation.SimulatedArm",side_effect=virtual_arm), \
                 patch("stream_live.device_class",return_value=lambda *_:camera), \
                 patch("vla_monitor.Monitor",return_value=monitor), \
                 patch("grasp_recorder.GraspRecorder",return_value=recorder), \
                 patch("run_exit_report.record_run_directory"), redirect_stdout(textio.StringIO()):
                self.assertEqual(ep.run_episode(93,180,.5,dry_run=True,manifest_path=manifest),0)
            guard.assert_called_once_with(ep.deny_serial)
            camera.open_cameras.assert_called_once()
            camera.close.assert_called_once()
            self.assertEqual(camera.fresh_frames.call_count,3)
            monitor.capture.assert_any_call(roles, unittest.mock.ANY, preview=True)
            self.assertEqual(recorder.goal.call_count,2)
            self.assertEqual(recorder.capture.call_count,2)
            output,=list((root/"runs").glob("*/summary.json"))
            summary=json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual((summary["replay_episode"],summary["replay_end"],summary["physical_motor_writes"]),
                             (93,"source_exhausted",0))
            self.assertFalse(summary["model_used"])
            self.assertEqual(summary["camera_preflight"]["status"],"passed")
            self.assertEqual(summary["camera_preflight"]["last_valid_roles"],sorted(roles))
            self.assertEqual([row[0] for row in summary["simulated_start_writes"]],["Goal_Position","Torque_Enable"])

    def test_live_near_black_never_reaches_startup_or_uses_camera_frames_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            manifest,_,reference=self.recording(root,[CONFIG["home"]["state"][:]])
            arm=self.arm([],"all_off")
            io=Mock()
            io.clock,io.pause,io.checked=arm.clock,arm.pause,arm.checked
            io.goal_attempted=False
            io.cleanup_errors=[]
            io.bus=None
            io.dark_cameras={"ceiling_oblique"}
            io.camera_frames.return_value=dict.fromkeys(CONFIG["cameras"],object())
            io.fresh_frames.return_value=None
            io.cameras_stats.summary.return_value={}
            io.thermal_window.summary.return_value={}
            io.thermal_diagnostics.summary.return_value={}
            io.communication.summary.return_value={}
            io.thermal_recheck={}
            io.torque_on_writes=io.torque_off_writes=0
            monitor=Mock()
            monitor.hold_requested.return_value=False

            with patch.dict(os.environ,{"SO101_DIRECT_OPERATOR":"1"}), \
                 patch.object(ep,"ROOT",root), patch.object(ep,"PUBLIC_REFERENCE",reference), \
                 patch.object(ep,"replay_context",return_value={}), \
                 patch("stream_live.device_class",return_value=lambda *_:io), \
                 patch("vla_monitor.Monitor",return_value=monitor), \
                 patch("grasp_recorder.GraspRecorder") as recorder, patch.object(ep,"replay") as replay, \
                 patch("run_exit_report.record_run_directory"), redirect_stdout(textio.StringIO()):
                self.assertEqual(ep.run_episode(93,180,.5,dry_run=False,manifest_path=manifest),1)
            recorder.assert_not_called()
            replay.assert_not_called()
            io.camera_frames.assert_not_called()
            io.close.assert_called_once()
            self.assertEqual(arm.start_writes,[])
            run,=list((root/"runs").iterdir())
            summary=json.loads((run/"summary.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["status"],summary["replay_end"],summary["steps"],summary["torque_register_writes"]),
                             ("HOLD","not_started",0,0))
            receipt=summary["camera_preflight"]
            self.assertEqual((receipt["status"],receipt["reason"],receipt["dark_roles_seen"]),
                             ("timed_out","camera_near_black",["ceiling_oblique"]))
            self.assertAlmostEqual(receipt["elapsed_s"],5.,places=6)
            rows=[json.loads(line) for line in (run/"steps.jsonl").read_text(encoding="utf-8").splitlines()]
            row,=rows
            self.assertEqual(row,{"event":"replay_camera_preflight",**receipt})

    def test_dry_serial_guard_denies_linux_serial_without_blocking_video(self):
        for path in ("/dev/ttyACM0",b"/dev/serial/by-id/fixture"):
            with self.assertRaisesRegex(RuntimeError,"physical serial"):
                ep.deny_serial("open",(path,))
        ep.deny_serial("open",("/dev/video0",))


if __name__ == "__main__":
    unittest.main()
