import json
import importlib.util
import io
import os
import multiprocessing
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from grasp_recorder import CAMERAS, GraspRecorder, jpeg_rgb


JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


class Pixels:
    def __init__(self, number): self.number = number
    def copy(self): return Pixels(self.number)


def encode(pixels):
    return b'JPEG-RGB-' + str(pixels.number).encode('ascii')


def encode_pid(pixels):
    return json.dumps(dict(pid=os.getpid(), number=pixels.number)).encode('ascii')


def encode_slow(pixels):
    time.sleep(5)
    return encode(pixels)


def encode_failure(pixels):
    raise OSError('process encoder failure')


def send_partial_pipe(connection, ready):
    # Deliberately send the Connection length header without its body.
    # The relay should block here; the motor-side snapshot poll must not.
    connection._send(struct.pack('!i', 1024*1024))
    ready.set()
    time.sleep(10)


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
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=encode, max_pending_frames=256, backend='thread')

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
        self.recorder = GraspRecorder(self.folder.name, JOINTS, encoder=failed, backend='thread')
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
                                      max_queue=4, max_pending_frames=2, backend='thread')
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
                                      max_queue=1, max_pending_frames=3, backend='thread')
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


class ProcessGraspRecorderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.close(timeout=.5)
        self.folder.cleanup()

    def recorder(self, **kwargs):
        settings = dict(encoder=encode_pid, max_pending_frames=128, frame_slot_bytes=4096)
        settings.update(kwargs)
        recorder = GraspRecorder(self.folder.name, JOINTS, **settings)
        self.recorders.append(recorder)
        return recorder

    def test_default_backend_is_spawn_process_and_flush_has_no_event_or_frame_loss(self):
        recorder = self.recorder()
        recorder.goal(40, telemetry(40), time_s=0, monotonic=0, unix=1000)
        for i in range(71):
            now = i/10
            recorder.capture(frames(now), {}, time_s=now, monotonic=now, unix=1000+now)
            recorder.goal(40 if i < 40 else 36, telemetry(40 if i < 40 else 36),
                          time_s=now, monotonic=now, unix=1000+now)
        result = recorder.close(timeout=10)
        self.assertEqual(result['backend'], 'process')
        self.assertFalse(result['writer_alive'])
        self.assertTrue(result['client']['shared_memory_unlinked'])
        self.assertEqual(result['client']['queue_cleanup'], 'closed')
        self.assertFalse(result['client']['forced_terminate'])
        self.assertEqual(result['worker']['parent_pid'], os.getpid())
        self.assertNotEqual(result['worker']['pid'], os.getpid())
        self.assertEqual(result['counts']['capture_batches'], 71)
        self.assertEqual(result['counts']['encoded_images'], 71*3)
        self.assertEqual(result['counts']['dropped_frames'], 0)
        self.assertEqual(result['events'][0]['status'], 'complete')
        event = json.loads((Path(self.folder.name)/'grasp_1/event.json').read_text(encoding='utf-8'))
        image = next(iter(event['frames'][0]['images'].values()))
        encoded = json.loads((Path(self.folder.name)/'grasp_1'/image).read_text(encoding='ascii'))
        self.assertEqual(encoded['pid'], result['worker']['pid'])

    def test_worker_encoding_error_visible_and_partial_event_still_flushed(self):
        recorder = self.recorder(encoder=encode_failure)
        recorder.capture(frames(0), {}, time_s=0, monotonic=0)
        recorder.goal(40, telemetry(40), time_s=0)
        recorder.goal(36, telemetry(36), time_s=.1)
        result = recorder.close()
        self.assertEqual(result['counts']['io_errors'], 1)
        self.assertEqual(result['events'][0]['status'], 'partial')
        self.assertIn('process encoder failure', result['errors'][0]['error'])

    def test_flush_timeout_terminates_only_evidence_process_and_releases_shared_memory(self):
        recorder = self.recorder(encoder=encode_slow)
        recorder.capture(frames(0), {}, time_s=0, monotonic=0)
        recorder.goal(40, telemetry(40), time_s=0)
        recorder.goal(36, telemetry(36), time_s=.1)
        started = time.monotonic()
        result = recorder.close(timeout=.05)
        self.assertLess(time.monotonic()-started, 2)
        self.assertFalse(result['writer_alive'])
        self.assertTrue(result['client']['forced_terminate'])
        self.assertTrue(result['client']['shared_memory_unlinked'])
        self.assertEqual(result['events'][0]['status'], 'evidence_flush_timeout')

    def test_unexpected_child_death_records_error_and_pending_event_failure(self):
        recorder = self.recorder(encoder=encode_slow)
        recorder.capture(frames(0), {}, time_s=0, monotonic=0)
        recorder.goal(40, telemetry(40), time_s=0)
        recorder.goal(36, telemetry(36), time_s=.1)
        recorder.thread.terminate()
        recorder.thread.join(timeout=1)
        started = time.monotonic()
        result = recorder.summary()
        self.assertLess(time.monotonic()-started, .2)
        self.assertGreater(result['counts']['io_errors'], 0)
        self.assertEqual(result['events'][0]['status'], 'evidence_worker_failed')
        self.assertIn('worker_exit', [row['operation'] for row in result['errors']])
        result = recorder.close()
        self.assertFalse(result['client']['relay_alive_after_close'])

    @unittest.skipIf(os.name == 'nt', 'raw pipe-header injection uses POSIX Connection; generic stalled relay tested on Windows')
    def test_partial_pipe_body_worker_death_does_not_block_main_poll(self):
        context = multiprocessing.get_context('spawn')
        receiver, writer = context.Pipe(duplex=False)
        ready = context.Event()
        process = context.Process(target=send_partial_pipe, args=(writer, ready))
        process.start()
        writer.close()
        relay = self._relay_probe(receiver)
        try:
            self.assertTrue(ready.wait(2))
            started = time.monotonic()
            for _ in range(1000): relay._poll_worker()
            self.assertLess(time.monotonic()-started, .2)
            process.terminate()
            process.join(timeout=1)
            relay.relay_thread.join(timeout=1)
            self.assertFalse(relay.relay_thread.is_alive())
        finally:
            if process.is_alive(): process.terminate()
            process.join(timeout=1)
            receiver.close()

    def _relay_probe(self, receiver):
        relay = GraspRecorder.__new__(GraspRecorder)
        relay._is_writer = relay._cleaned = relay.closed = False
        relay._latest_worker_update = relay._applied_worker_update = relay._relay_error = None
        relay._unexpected_exit_reported = False
        relay.result_queue = object()
        relay.result_receiver = receiver
        relay.relay_stop = threading.Event()
        relay.thread = SimpleNamespace(exitcode=None)
        relay.relay_thread = threading.Thread(target=relay._receive_updates, daemon=True)
        relay.relay_thread.start()
        return relay

    def test_stalled_result_body_is_confined_to_daemon_relay(self):
        entered, release = threading.Event(), threading.Event()
        class StalledReceiver:
            def recv(self):
                entered.set()
                release.wait(3)
                raise EOFError
        relay = self._relay_probe(StalledReceiver())
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            for _ in range(1000): relay._poll_worker()
            self.assertLess(time.monotonic()-started, .2)
        finally:
            release.set()
            relay.relay_thread.join(timeout=1)
        self.assertFalse(relay.relay_thread.is_alive())

    @unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('numpy'),
                         'real shared RGB path runs in Jetson Pillow/numpy environment')
    def test_real_rgb_pixels_use_shared_slots_and_encode_only_in_child(self):
        import numpy as np
        from PIL import Image
        recorder = self.recorder(encoder=jpeg_rgb)
        pixels = np.zeros((16, 24, 3), dtype=np.uint8)
        pixels[:, :, 0] = 240
        recorder.goal(40, telemetry(40), time_s=0)
        snapshot = {name: dict(pixels=pixels, timestamp=.1) for name in CAMERAS}
        recorder.capture(snapshot, {}, time_s=.1, monotonic=.1)
        pixels[:] = 0  # Parent changes cannot alter a queued shared snapshot.
        recorder.goal(36, telemetry(36), time_s=.2)
        result = recorder.close()
        self.assertEqual(result['client']['shared_copy_bytes'], 3*16*24*3)
        event = json.loads((Path(self.folder.name)/'grasp_1/event.json').read_text(encoding='utf-8'))
        for relative in event['frames'][0]['images'].values():
            image = Image.open(Path(self.folder.name)/'grasp_1'/relative)
            self.assertGreater(image.getpixel((12, 8))[0], 220)


if __name__ == '__main__': unittest.main()
