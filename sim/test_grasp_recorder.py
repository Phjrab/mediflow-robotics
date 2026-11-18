import json
import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path

from grasp_recorder import CAMERAS, GraspRecorder, jpeg_rgb


JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


class Pixels:
    def __init__(self, number): self.number = number
    def copy(self): return Pixels(self.number)


def encode(pixels):
    return b'JPEG-RGB-' + str(pixels.number).encode('ascii')


def frames(now):
    return {name: dict(pixels=Pixels(now), timestamp=now) for name in CAMERAS}


def telemetry(target, step=1):
    return dict(step=step, model=[0]*5+[target], wire_raw=dict(gripper=500),
                wire_normalized=[0]*5+[target], feedback=dict(state=[0]*5+[target],
                    present_raw=dict(gripper=501), loads=dict(gripper=5)),
                lateral_correction=dict(applied_deg=.527),
                tracking_compensation=dict(offset_deg=[0]*6))


class GraspRecorderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=encode, max_pending_frames=256)

    def tearDown(self):
        self.recorder.close()
        self.folder.cleanup()

    def send(self, now, target):
        return self.recorder.goal(target, telemetry(target), time_s=now, monotonic=now, unix=1000+now)

    def capture(self, now, **kwargs):
        return self.recorder.capture(frames(now), {'state': [0]*6},
                                     time_s=now, monotonic=now, unix=1000+now, **kwargs)

    def read(self, number=1):
        return json.loads((Path(self.folder.name)/f'grasp_{number}'/'event.json').read_text(encoding='utf-8'))

    def test_pre_three_post_two_seconds_and_exact_goal_metadata(self):
        self.send(0, 40)
        for i in range(71):
            now = i/10
            self.capture(now)
            self.send(now, 40 if i < 40 else 36)
        result = self.recorder.close()
        event = self.read()
        self.assertEqual(result['event_count'], 1)
        self.assertEqual(event['status'], 'complete')
        self.assertEqual(event['time_s'], 4)
        times = [row['time_s'] for row in event['frames']]
        self.assertGreaterEqual(min(times), 1)
        self.assertLessEqual(max(times), 6)
        self.assertEqual(len(times), 51)
        self.assertEqual(event['trigger']['wire_raw']['gripper'], 500)
        self.assertEqual(event['trigger']['feedback']['present_raw']['gripper'], 501)
        self.assertEqual(event['trigger']['lateral_correction']['applied_deg'], .527)
        self.assertTrue(all(set(row['images']) == set(CAMERAS) for row in event['frames']))
        for row in event['frames']:
            for path in row['images'].values():
                self.assertTrue((Path(self.folder.name)/'grasp_1'/path).read_bytes().startswith(b'JPEG-RGB-'))

    def test_slow_cumulative_close_one_event_then_rearm_on_open(self):
        self.send(0, 40)
        for i in range(1, 31): self.send(i/10, 40-i*.2)
        self.assertEqual(self.recorder.summary()['event_count'], 1)
        self.send(4, 36)  # Two point reopening does not rearm.
        self.send(4.1, 31)
        self.assertEqual(self.recorder.summary()['event_count'], 1)
        self.send(4.2, 40)
        self.send(4.3, 37)  # Strictly greater than three, not >=.
        self.assertEqual(self.recorder.summary()['event_count'], 1)
        self.send(4.4, 36.9)
        self.assertEqual(self.recorder.summary()['event_count'], 2)

    def test_close_finalizes_partial_window_after_interrupt(self):
        self.send(0, 40)
        self.capture(.1)
        self.send(.2, 36)
        self.capture(.3)
        result = self.recorder.close(reason='Ctrl+C')
        self.assertFalse(result['writer_alive'])
        self.assertEqual(self.read()['status'], 'partial')
        self.assertEqual(self.read()['end_reason'], 'Ctrl+C')

    def test_duplicates_stale_frames_and_capture_rate_not_relabelled(self):
        original = frames(1)
        for now in (1, 1.01, 1.2, 2):
            self.recorder.capture(original, {}, time_s=now, monotonic=now, unix=1000+now)
        result = self.recorder.close()
        self.assertEqual(result['counts']['encoded_images'], 3)
        self.assertEqual(result['counts']['duplicate_images'], 3)
        self.assertEqual(result['counts']['stale_images'], 3)

    def test_missing_camera_reported_without_fabricated_image(self):
        self.send(0, 40)
        partial = frames(.1)
        partial.pop('end_effector')
        self.recorder.capture(partial, {}, time_s=.1, monotonic=.1, unix=1000.1)
        self.send(.2, 36)
        result = self.recorder.close()
        self.assertEqual(result['counts']['missing_images'], 1)
        self.assertNotIn('end_effector', self.read()['frames'][0]['images'])
        self.assertFalse(self.read()['camera_coverage']['end_effector']['window_covered'])

    def test_writer_failure_is_reported_without_raising_into_motor_loop(self):
        self.recorder.close()
        def failed(_): raise OSError('test disk/encoder failure')
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=failed)
        self.capture(0)
        self.send(0, 40)
        self.send(.1, 36)
        result = self.recorder.close()
        self.assertEqual(result['counts']['io_errors'], 1)
        self.assertEqual(result['event_count'], 1)
        self.assertEqual(self.read()['frames'], [])

    def test_slow_writer_has_bounded_frame_queue_and_visible_drops(self):
        self.recorder.close()
        entered, release = threading.Event(), threading.Event()
        def blocked(pixels):
            entered.set()
            release.wait(3)
            return encode(pixels)
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=blocked,
                                      max_queue=4, max_pending_frames=2)
        self.capture(0)
        self.assertTrue(entered.wait(1))
        for i in range(1, 10): self.capture(i/10)
        self.assertLessEqual(self.recorder.pending_frames, 2)
        self.assertGreater(self.recorder.summary()['counts']['dropped_frames'], 0)
        release.set()
        self.assertFalse(self.recorder.close()['writer_alive'])

    def test_overlap_uses_same_encoded_frames_and_keeps_separate_events(self):
        self.send(0, 40)
        for i in range(41):
            now = i/10
            self.capture(now)
            target = 36 if 10 <= i < 15 or i >= 20 else 40
            self.send(now, target)
        result = self.recorder.close()
        self.assertEqual(result['event_count'], 2)
        self.assertEqual(result['counts']['encoded_images'], 41*3)
        self.assertGreater(result['counts']['saved_images'], result['counts']['encoded_images'])
        self.assertEqual(self.read(1)['time_s'], 1)
        self.assertEqual(self.read(2)['time_s'], 2)
        self.assertEqual(len([row for row in self.read(1)['goals'] if row['time_s'] == 2]), 1)

    def test_fixed_grid_achieves_ten_fps_at_44hz_without_catchup_bursts(self):
        for i in range(441): self.capture(i/44)
        result = self.recorder.close()
        self.assertEqual(result['counts']['capture_batches'], 101)
        self.assertFalse(self.recorder.due(100))  # Closed recorder cannot collect.

    def test_long_frame_gap_is_incomplete_even_when_control_runs_to_end(self):
        self.send(0, 40)
        self.capture(0)
        self.send(1, 36)
        self.capture(1)
        self.capture(3)
        self.send(3.2, 36)
        self.recorder.close()
        self.assertEqual(self.read()['status'], 'incomplete')

    def test_full_queue_event_loss_stays_visible_in_summary(self):
        self.recorder.close()
        entered, release = threading.Event(), threading.Event()
        def blocked(pixels):
            entered.set()
            release.wait(3)
            return encode(pixels)
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=blocked,
                                      max_queue=1, max_pending_frames=3)
        self.capture(0)
        self.assertTrue(entered.wait(1))
        self.capture(.1)
        self.send(.1, 40)
        self.send(.2, 36)
        self.assertEqual(self.recorder.summary()['events'][0]['status'], 'evidence_queue_full')
        self.assertEqual(self.recorder.summary()['counts']['dropped_events'], 1)
        release.set()
        self.recorder.close()

    @unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy'),
                         'actual JPEG check runs in the Jetson Pillow/numpy environment')
    def test_actual_jpeg_preserves_rgb_channel_order(self):
        import numpy as np
        from PIL import Image
        pixels = np.zeros((16, 24, 3), dtype=np.uint8)
        pixels[:, :, 0] = 240
        encoded = jpeg_rgb(pixels)
        image = Image.open(io.BytesIO(encoded))
        self.assertEqual(image.size, (24, 16))
        red, green, blue = image.getpixel((12, 8))
        self.assertGreater(red, 220)
        self.assertLess(green, 20)
        self.assertLess(blue, 20)


if __name__ == '__main__': unittest.main()
