"""Nonblocking, device-free evidence recording around transmitted gripper closes.

Increasing gripper points means opening on this calibrated robot. This recorder
observes successful goals; it never opens a device or changes a command.
"""
import copy
import io
import json
import math
import multiprocessing
from multiprocessing import shared_memory
import os
import queue
import signal
import threading
import time
from collections import deque
from pathlib import Path


CAMERAS = ('end_effector', 'ceiling_vertical', 'ceiling_oblique')


def _writer_process(settings, incoming, outgoing, stop, slots, shared_name, encoder, ready):
    """Spawned with no motor objects, descriptors, model, or CUDA state."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    priority = 'unchanged'
    if hasattr(os, 'nice'):
        try:
            priority = os.nice(5)
        except OSError:
            priority = 'nice_unavailable'
    writer = GraspRecorder.__new__(GraspRecorder)
    writer.__dict__.update(settings)
    writer._is_writer = True
    writer.queue, writer.result_queue, writer.stop = incoming, outgoing, stop
    writer.slot_states, writer.encoder = slots, encoder
    writer.ready = ready
    writer.shared = shared_memory.SharedMemory(name=shared_name) if shared_name else None
    writer.lock = threading.Lock()
    writer.events, writer.errors = [], []
    writer.counts = dict.fromkeys(settings['counts'], 0)
    writer.last_time_s = writer.end_reason = None
    writer.thread = threading.current_thread()
    writer.worker_metrics = dict(pid=os.getpid(), parent_pid=os.getppid(), nice=priority,
                                 encode_s=0., write_s=0., cpu_s=0., wall_s=0., cpu_scope='process')
    writer._publish_at = 0.
    try:
        writer._worker()
    finally:
        writer._publish(force=True)
        if writer.shared is not None:
            writer.shared.close()
        outgoing.close()


def jpeg_rgb(pixels):
    from PIL import Image
    output = io.BytesIO()
    Image.fromarray(pixels, mode='RGB').save(output, 'JPEG', quality=85)
    return output.getvalue()


def feedback_fields(sample):
    return {key: copy.deepcopy(sample[key]) for key in
            ('state', 'present_raw', 'loads', 'currents', 'current_raw', 'temperatures',
             'statuses', 'voltage_v', 'torque')
            if key in sample}


class GraspRecorder:
    """Call capture at each loop and goal only after successful transmission.

    Frames are copied at at most 10 Hz into bounded shared-memory slots. A
    spawn-created process encodes and writes them without sharing the motor
    interpreter's GIL or inherited serial/CUDA state. The explicitly selected
    thread backend is retained only for controlled comparison tests.
    Frames are encoded once in a bounded worker queue,
    and retained as JPEG bytes for five seconds. On an evidence I/O failure the
    control loop continues and the summary explicitly reports lost evidence.
    """
    def __init__(self, output, joints, *, fps=10, ring_seconds=5, pre_seconds=3,
                 post_seconds=2, close_points=3, max_queue=256, max_pending_frames=8,
                 frame_max_age=.5, encoder=jpeg_rgb, clock=time.monotonic,
                 wall_clock=time.time, cameras=CAMERAS, backend='process',
                 frame_slot_bytes=640*480*3, startup_timeout=10):
        if min(fps, ring_seconds, pre_seconds, post_seconds, close_points) <= 0:
            raise ValueError('recording durations, rate, and threshold must be positive')
        if ring_seconds < pre_seconds or max_queue < 1 or max_pending_frames < 1:
            raise ValueError('ring must cover the pre-event window and queue must be bounded')
        if backend not in ('process', 'thread'):
            raise ValueError('recording backend must be process or thread')
        self.backend, self._is_writer = backend, False
        self.output, self.joints = Path(output), tuple(joints)
        self.fps, self.ring_seconds = fps, ring_seconds
        self.pre_seconds, self.post_seconds, self.close_points = pre_seconds, post_seconds, close_points
        self.frame_max_age, self.encoder = frame_max_age, encoder
        self.clock, self.wall_clock, self.cameras = clock, wall_clock, tuple(cameras)
        self.max_pending_frames = max_pending_frames
        self._pending_frames = 0
        self.lock = threading.Lock()
        self.next_capture = float('-inf')
        self.previous_frames = {}
        self.previous_target = self.peak_target = self.trough_target = None
        self.closing_started = None
        self.armed = True
        self.goal_ring = deque()
        self.latest_goal = None
        self.events = []
        self.counts = dict(capture_batches=0, captured_images=0, duplicate_images=0,
                           stale_images=0, missing_images=0, dropped_frames=0,
                           dropped_telemetry=0, dropped_events=0, encoded_images=0,
                           saved_images=0, io_errors=0)
        self.errors = []
        self.closed = False
        self.end_reason = None
        self.last_time_s = None
        self.result_queue = self.shared = self.slot_states = None
        self.result_receiver = None
        self.relay_thread = None
        self.relay_stop = threading.Event()
        self._latest_worker_update = self._applied_worker_update = None
        self._relay_error = None
        self._unexpected_exit_reported = False
        self.frame_slot_bytes = frame_slot_bytes
        self.worker_counts = {}
        self.worker_errors = []
        self.worker_metrics = dict(encode_s=0., write_s=0., cpu_s=0., wall_s=0., cpu_scope='writer_thread')
        self.client_metrics = dict(capture_calls=0, capture_wall_s=0., capture_max_s=0.,
                                   enqueue_calls=0, enqueue_wall_s=0., enqueue_max_s=0.,
                                   shared_copy_bytes=0, queue_cleanup='pending',
                                   shared_memory_unlinked=False, forced_terminate=False)
        self._publish_at = 0.
        self._cleaned = False
        if backend == 'thread':
            self.queue = queue.Queue(maxsize=max_queue)
            self.stop = threading.Event()
            self.ready = threading.Event()
            self.thread = threading.Thread(target=self._worker, name='vla-grasp-evidence', daemon=True)
        else:
            context = multiprocessing.get_context('spawn')
            self.queue = context.Queue(maxsize=max_queue)
            self.result_receiver, self.result_queue = context.Pipe(duplex=False)
            self.stop = context.Event()
            self.ready = context.Event()
            self.slot_states = context.RawArray('b', max_pending_frames)
            self.shared = shared_memory.SharedMemory(create=True,
                size=max_pending_frames*len(self.cameras)*frame_slot_bytes)
            settings = {name: getattr(self, name) for name in
                        ('output', 'joints', 'fps', 'ring_seconds', 'pre_seconds', 'post_seconds',
                         'cameras', 'counts', 'frame_slot_bytes', 'max_pending_frames', 'backend')}
            self.thread = context.Process(target=_writer_process,
                args=(settings, self.queue, self.result_queue, self.stop, self.slot_states,
                      self.shared.name, encoder, self.ready), name='vla-grasp-evidence', daemon=True)
        try:
            self.thread.start()
            if backend == 'process':
                # The parent must not retain a writer. A child killed during a
                # partial IPC body must give the receiver EOF, not hang forever.
                self.result_queue.close()
                self.relay_thread = threading.Thread(target=self._receive_updates,
                    name='vla-grasp-results', daemon=True)
                self.relay_thread.start()
        except BaseException:
            if self.shared is not None:
                self.shared.close()
                self.shared.unlink()
            if self.result_queue is not None:
                self.queue.close()
                self.result_queue.close()
                self.result_receiver.close()
            raise
        startup_started = time.perf_counter()
        if not self.ready.wait(timeout=startup_timeout):
            self.close(reason='writer_startup_timeout', timeout=.2)
            raise RuntimeError('grasp evidence writer did not become ready before policy start')
        self.client_metrics['writer_ready_wait_s'] = time.perf_counter()-startup_started

    @property
    def pending_frames(self):
        return sum(self.slot_states) if self.slot_states is not None else self._pending_frames

    def _poll_worker(self):
        if self.result_queue is None or self._is_writer or self._cleaned:
            return
        # Never call Pipe/Queue receive on the motor thread. Even a nonblocking
        # Queue.get can block after a readable header but before the entire body.
        # The relay owns reception; published dictionaries are never mutated.
        update = self._latest_worker_update
        if update is not None and update is not self._applied_worker_update:
            self._applied_worker_update = update
            self.worker_counts = update['counts']
            self.worker_metrics = update['worker']
            for row in update['events']:
                if row['id'] <= len(self.events):
                    self.events[row['id']-1].update(row)
            self.worker_errors = update['errors']
        exitcode = self.thread.exitcode
        if exitcode is not None and (exitcode != 0 or not self.closed) and not self._unexpected_exit_reported:
            self._unexpected_exit_reported = True
            self._error('worker_exit', f'evidence process exited unexpectedly with code {exitcode}')
            for event in self.events:
                if event['status'] == 'queued':
                    event['status'] = 'evidence_worker_failed'

    def _receive_updates(self):
        try:
            while not self.relay_stop.is_set():
                update = self.result_receiver.recv()
                if isinstance(update, dict):
                    self._latest_worker_update = update
        except EOFError:
            pass
        except (OSError, ValueError) as error:
            if not self.closed:
                self._relay_error = str(error)

    def _publish(self, *, force=False):
        if self.result_queue is None:
            return
        now = time.monotonic()
        if not force and now < self._publish_at:
            return
        self._publish_at = now+1.
        data = self.summary()
        try:
            # Only the isolated writer process sends. Backpressure can delay
            # evidence work, but never the motor thread or its latest snapshot.
            self.result_queue.send(data)
        except (BrokenPipeError, EOFError, OSError):
            pass

    def due(self, monotonic=None):
        return not self.closed and (self.clock() if monotonic is None else monotonic)+1e-9 >= self.next_capture

    def _increment(self, key, amount=1):
        with self.lock:
            self.counts[key] += amount

    def _error(self, operation, error, event=None):
        with self.lock:
            self.counts['io_errors'] += 1
            if len(self.errors) < 20:
                self.errors.append(dict(operation=operation, error=str(error), event=event))

    def _enqueue(self, kind, data):
        started = time.perf_counter()
        try:
            self._poll_worker()
            if self.backend == 'process' and not self.thread.is_alive():
                self._increment({'frame': 'dropped_frames', 'event': 'dropped_events',
                                 'goal': 'dropped_telemetry'}[kind])
                return False
            self.queue.put_nowait((kind, data))
            return True
        except queue.Full:
            self._increment({'frame': 'dropped_frames', 'event': 'dropped_events',
                             'goal': 'dropped_telemetry'}[kind])
            return False
        finally:
            elapsed = time.perf_counter()-started
            self.client_metrics['enqueue_calls'] += 1
            self.client_metrics['enqueue_wall_s'] += elapsed
            self.client_metrics['enqueue_max_s'] = max(self.client_metrics['enqueue_max_s'], elapsed)

    def capture(self, frames, sample, *, time_s, monotonic=None, unix=None):
        started = time.perf_counter()
        try:
            return self._capture(frames, sample, time_s=time_s, monotonic=monotonic, unix=unix)
        finally:
            elapsed = time.perf_counter()-started
            self.client_metrics['capture_calls'] += 1
            self.client_metrics['capture_wall_s'] += elapsed
            self.client_metrics['capture_max_s'] = max(self.client_metrics['capture_max_s'], elapsed)

    def _copy_pixels(self, pixels, slot, camera_index):
        if self.shared is None or not hasattr(pixels, 'nbytes'):
            return pixels.copy()
        if pixels.dtype.kind != 'u' or pixels.dtype.itemsize != 1 or len(pixels.shape) != 3 or pixels.shape[-1] != 3:
            raise ValueError('evidence image must be uint8 RGB HxWx3')
        size = pixels.nbytes
        if size > self.frame_slot_bytes:
            raise ValueError(f'frame exceeds fixed shared-memory slot: {size}>{self.frame_slot_bytes}')
        offset = (slot*len(self.cameras)+camera_index)*self.frame_slot_bytes
        try:
            source = memoryview(pixels).cast('B')
        except TypeError:
            source = pixels.tobytes(order='C')
        self.shared.buf[offset:offset+size] = source
        if isinstance(source, memoryview):
            source.release()
        self.client_metrics['shared_copy_bytes'] += size
        return dict(shared_offset=offset, shared_bytes=size, shape=tuple(pixels.shape), dtype=pixels.dtype.str)

    def _release_slot(self, slot):
        if self.slot_states is not None:
            self.slot_states[slot] = 0
        else:
            with self.lock:
                self._pending_frames -= 1

    def _capture(self, frames, sample, *, time_s, monotonic=None, unix=None):
        now = self.clock() if monotonic is None else monotonic
        if not self.due(now):
            return False
        interval = 1 / self.fps
        if not math.isfinite(self.next_capture):
            self.next_capture = now + interval
        else:
            # Keep a fixed sampling grid. Scheduling now+0.1 drifts to ~9 Hz
            # inside a 44 Hz control loop; missed slots are skipped, not replayed.
            slots = math.floor((now-self.next_capture+1e-9) / interval) + 1
            self.next_capture += max(1, slots) * interval
        self.last_time_s = time_s
        with self.lock:
            full = self.pending_frames >= self.max_pending_frames
        if full:
            self._increment('dropped_frames')
            return False
        slot = None
        if self.slot_states is not None:
            slot = next((i for i, occupied in enumerate(self.slot_states) if not occupied), None)
            if slot is None:
                self._increment('dropped_frames')
                return False
            self.slot_states[slot] = 1
        images, sources = {}, {}
        for camera_index, name in enumerate(self.cameras):
            frame = (frames or {}).get(name)
            if not frame or frame.get('pixels') is None:
                self._increment('missing_images')
                continue
            pixels = frame['pixels']
            captured = frame.get('timestamp')
            # Camera producer replaces arrays, so identity is a fallback only.
            token = ('timestamp', captured) if captured is not None else ('identity', id(pixels))
            if captured is not None and (not math.isfinite(captured) or now-captured > self.frame_max_age):
                self._increment('stale_images')
                continue
            if self.previous_frames.get(name) == token:
                self._increment('duplicate_images')
                continue
            try:
                images[name] = self._copy_pixels(pixels, slot, camera_index)
            except Exception as error:
                self._error('frame_copy', error)
                continue
            sources[name] = dict(camera_monotonic_s=captured,
                                 age_s=None if captured is None else now-captured,
                                 timestamp_verified=captured is not None)
            self.previous_frames[name] = token
        if not images:
            if slot is not None:
                self._release_slot(slot)
            return False
        data = dict(time_s=time_s, monotonic_s=now,
                    unix=self.wall_clock() if unix is None else unix,
                    feedback=feedback_fields(sample), previous_goal=copy.deepcopy(self.latest_goal),
                    sources=sources, pixels=images, shared_slot=slot)
        if self.slot_states is None:
            with self.lock:
                self._pending_frames += 1
        if not self._enqueue('frame', data):
            self._release_slot(slot)
            return False
        self._increment('capture_batches')
        self._increment('captured_images', len(images))
        return True

    def goal(self, sent_gripper_points, telemetry, *, time_s, monotonic=None, unix=None):
        """A >3-point cumulative close creates one event, until >3-point reopening."""
        if self.closed or not math.isfinite(sent_gripper_points):
            return None
        now = self.clock() if monotonic is None else monotonic
        timestamp = self.wall_clock() if unix is None else unix
        self.last_time_s = time_s
        row = {key: copy.deepcopy(telemetry[key]) for key in
               ('step', 'model_phase', 'playback_s', 'model', 'wire_raw', 'wire_normalized',
                'lateral_correction', 'tracking_compensation', 'grip_relief', 'gripper_opening')
               if key in telemetry}
        row.update(time_s=time_s, monotonic_s=now, unix=timestamp,
                   sent_gripper_points=sent_gripper_points,
                   feedback=feedback_fields(telemetry.get('feedback', {})))
        self.latest_goal = row
        self.goal_ring.append(row)
        while self.goal_ring and self.goal_ring[0]['time_s'] < time_s-self.ring_seconds:
            self.goal_ring.popleft()
        if self.previous_target is None:
            self.previous_target = self.peak_target = self.trough_target = sent_gripper_points
        if self.armed:
            if sent_gripper_points >= self.peak_target:
                self.peak_target = sent_gripper_points
                self.closing_started = None
            elif self.closing_started is None:
                self.closing_started = dict(time_s=time_s, monotonic_s=now, unix=timestamp)
        else:
            self.trough_target = min(self.trough_target, sent_gripper_points)
            if sent_gripper_points-self.trough_target > self.close_points:
                self.armed, self.peak_target, self.closing_started = True, sent_gripper_points, None
        event = None
        if self.armed and self.peak_target-sent_gripper_points > self.close_points:
            self.armed, self.trough_target = False, sent_gripper_points
            number = len(self.events)+1
            event = dict(id=number, directory=f'grasp_{number}', time_s=time_s, unix=timestamp,
                         monotonic_s=now, closing_started=copy.deepcopy(self.closing_started),
                         trigger_drop_points=self.peak_target-sent_gripper_points,
                         trigger_reference_points=self.peak_target, trigger=row,
                         window_start_s=time_s-self.pre_seconds,
                         window_end_s=time_s+self.post_seconds, status='queued')
            with self.lock:
                self.events.append(event)
            payload = dict(event=event, goals=[r for r in self.goal_ring
                                             if r['time_s'] >= event['window_start_s']])
            if not self._enqueue('event', payload):
                with self.lock:
                    event['status'] = 'evidence_queue_full'
        else:
            # Only post-event telemetry needs to cross the worker boundary.
            if any(e['time_s'] < time_s <= e['window_end_s'] for e in self.events):
                if not self._enqueue('goal', row):
                    with self.lock:
                        for prior in self.events:
                            if prior['time_s'] < time_s <= prior['window_end_s']:
                                prior.setdefault('evidence_issues', []).append(
                                    dict(operation='goal', error='telemetry_queue_full', time_s=time_s))
        self.previous_target = sent_gripper_points
        return None if event is None else dict(id=event['id'], time_s=time_s, unix=timestamp)

    def _save_batch(self, active, batch):
        write_started = time.perf_counter()
        event = active['event']
        if not event['window_start_s']-1e-8 <= batch['time_s'] <= event['window_end_s']+1e-8:
            return
        record = {key: value for key, value in batch.items() if key != 'jpeg'}
        record['images'] = {}
        for name, encoded in batch['jpeg'].items():
            relative = Path(name) / f"{active['frame_count']:05d}_{batch['time_s']-event['time_s']:+08.3f}.jpg"
            path = active['path'] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
            record['images'][name] = relative.as_posix()
            self._increment('saved_images')
        active['frames'].append(record)
        active['frame_count'] += 1
        self.worker_metrics['write_s'] += time.perf_counter()-write_started

    def _finish_event(self, active, *, partial, reason):
        write_started = time.perf_counter()
        event = active['event']
        camera_coverage = {}
        start = max(0, event['window_start_s'])
        end = min(event['window_end_s'], self.last_time_s or event['window_end_s'])
        tolerance = 1.5 / self.fps
        for name in self.cameras:
            times = [row['time_s'] for row in active['frames'] if name in row['images']]
            gaps = [right-left for left, right in zip(times, times[1:])]
            camera_coverage[name] = dict(frames=len(times), first_time_s=times[0] if times else None,
                last_time_s=times[-1] if times else None, max_gap_s=max(gaps, default=0),
                window_covered=bool(times and times[0] <= start+tolerance+1e-8
                    and times[-1] >= end-tolerance-1e-8 and max(gaps, default=0) <= tolerance+1e-8))
        evidence_issues = list(event.get('evidence_issues', []))+list(active.get('evidence_issues', []))
        gaps = bool(evidence_issues) or not all(v['window_covered'] for v in camera_coverage.values())
        status = 'partial' if partial else 'incomplete' if gaps else 'complete'
        payload = {**copy.deepcopy(event), 'schema': 'so101.grasp_evidence.v1',
                   'joints': self.joints, 'cameras': self.cameras, 'fps': self.fps,
                   'ring_seconds': self.ring_seconds, 'pre_seconds': self.pre_seconds,
                   'post_seconds': self.post_seconds, 'status': status,
                   'end_reason': reason, 'frames': active['frames'], 'goals': active['goals'],
                   'camera_coverage': camera_coverage, 'evidence_issues': evidence_issues,
                   'pre_window_available_s': min(self.pre_seconds,
                       max(0, event['time_s']-active['frames'][0]['time_s'])) if active['frames'] else 0,
                   'last_frame_time_s': active['frames'][-1]['time_s'] if active['frames'] else None,
                   'evidence_counts_at_finish': self.summary()['counts']}
        active['path'].mkdir(parents=True, exist_ok=True)
        destination = active['path']/'event.json'
        pending = destination.with_suffix('.pending')
        pending.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        pending.replace(destination)
        with self.lock:
            event['status'] = payload['status']
            event['frames'] = len(active['frames'])
            event['goals'] = len(active['goals'])
        self.worker_metrics['write_s'] += time.perf_counter()-write_started
        self._publish(force=True)

    def _encode_input(self, data):
        output = {}
        for name, pixels in data['pixels'].items():
            if isinstance(pixels, dict) and 'shared_offset' in pixels:
                import numpy as np
                pixels = np.ndarray(pixels['shape'], dtype=pixels['dtype'],
                                    buffer=self.shared.buf, offset=pixels['shared_offset'])
            output[name] = self.encoder(pixels)
        return output

    def _receive_close(self, data):
        self.end_reason, self.last_time_s = data['reason'], data['last_time_s']
        if self._is_writer:
            self.client_counts_at_close = data.get('counts', {})
            for record in data.get('events', []):
                if record.get('evidence_issues'):
                    event = next((row for row in self.events if row['id'] == record['id']), None)
                    if event is not None:
                        event['evidence_issues'] = record['evidence_issues']
                        destination = self.output/event['directory']/'event.json'
                        if destination.is_file():
                            payload = json.loads(destination.read_text(encoding='utf-8'))
                            payload.setdefault('evidence_issues', []).extend(record['evidence_issues'])
                            if payload['status'] == 'complete':
                                payload['status'] = event['status'] = 'incomplete'
                            destination.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                                              indent=2), encoding='utf-8')

    def _worker(self):
        ring, active = deque(), []
        wall_started = time.perf_counter()
        cpu_clock = time.process_time if self._is_writer else (
            time.thread_time if hasattr(time, 'thread_time') else time.process_time)
        cpu_started = cpu_clock()
        try:
            if self.encoder is jpeg_rgb:
                # This runs before the parent declares READY, never while the
                # first policy goals are waiting behind module/PIL initialization.
                import numpy as np
                jpeg_rgb(np.zeros((1, 1, 3), dtype=np.uint8))
            self.ready.set()
            while True:
                try:
                    kind, data = self.queue.get(timeout=.05)
                except queue.Empty:
                    if self.stop.is_set():
                        break
                    continue
                try:
                    if kind == 'close':
                        self._receive_close(data)
                        break
                    now_s = data['event']['time_s'] if kind == 'event' else data['time_s']
                    if self._is_writer:
                        self.last_time_s = now_s
                    for item in tuple(active):
                        if now_s > item['event']['window_end_s']+1e-8:
                            self._finish_event(item, partial=False, reason='post_window_complete')
                            active.remove(item)
                    if kind == 'frame':
                        batch = {key: value for key, value in data.items() if key != 'pixels'}
                        encode_started = time.perf_counter()
                        batch['jpeg'] = self._encode_input(data)
                        self.worker_metrics['encode_s'] += time.perf_counter()-encode_started
                        self._increment('encoded_images', len(batch['jpeg']))
                        ring.append(batch)
                        while ring and ring[0]['time_s'] < now_s-self.ring_seconds:
                            ring.popleft()
                        for item in active:
                            self._save_batch(item, batch)
                    elif kind == 'event':
                        event = data['event']
                        if self._is_writer:
                            self.events.append(event)
                        # A new close may overlap an earlier event's post window.
                        # Its successful goal belongs to both windows, once each.
                        for earlier in active:
                            if event['time_s'] <= earlier['event']['window_end_s']+1e-8:
                                earlier['goals'].append(event['trigger'])
                        item = dict(event=event, path=self.output/event['directory'], frames=[],
                                    goals=data['goals'], frame_count=0)
                        active.append(item)
                        for batch in ring:
                            self._save_batch(item, batch)
                    elif kind == 'goal':
                        for item in active:
                            if data['time_s'] <= item['event']['window_end_s']+1e-8:
                                item['goals'].append(data)
                except Exception as error:
                    self._error(kind, error)
                    for item in active:
                        item.setdefault('evidence_issues', []).append(dict(operation=kind, error=str(error)))
                finally:
                    if kind == 'frame':
                        self._release_slot(data.get('shared_slot'))
                    if hasattr(self.queue, 'task_done'):
                        self.queue.task_done()
                    self.worker_metrics['cpu_s'] = cpu_clock()-cpu_started
                    self.worker_metrics['wall_s'] = time.perf_counter()-wall_started
                    self._publish()
            for item in active:
                try:
                    partial = self.last_time_s is None or self.last_time_s < item['event']['window_end_s']
                    self._finish_event(item, partial=partial, reason=self.end_reason or 'run_end')
                except Exception as error:
                    self._error('finalize', error, item['event']['id'])
                    with self.lock:
                        item['event']['status'] = 'evidence_write_failed'
        except Exception as error:
            self._error('worker', error)
        finally:
            self.worker_metrics['cpu_s'] = cpu_clock()-cpu_started
            self.worker_metrics['wall_s'] = time.perf_counter()-wall_started
            self._publish(force=True)

    def summary(self):
        self._poll_worker()
        with self.lock:
            events = [{key: copy.deepcopy(event.get(key)) for key in
                       ('id', 'directory', 'time_s', 'unix', 'status', 'frames', 'goals',
                        'trigger_drop_points')} for event in self.events]
            counts = {key: value+self.worker_counts.get(key, 0) for key, value in self.counts.items()} if not self._is_writer else dict(self.counts)
            return dict(event_count=len(events), events=events, counts=counts,
                        errors=list(self.errors)+list(getattr(self, 'worker_errors', [])), fps=self.fps, ring_seconds=self.ring_seconds,
                        pre_seconds=self.pre_seconds, post_seconds=self.post_seconds,
                        writer_alive=self.thread.is_alive(), backend=self.backend,
                        worker=dict(self.worker_metrics),
                        client=dict(getattr(self, 'client_metrics', {})),
                        relay_error=getattr(self, '_relay_error', None))

    def close(self, *, reason='run_end', timeout=10):
        if self._cleaned:
            return self.summary()
        self.closed, self.end_reason = True, reason
        deadline = time.monotonic()+timeout
        message = dict(reason=reason, last_time_s=self.last_time_s,
                       events=[dict(id=e['id'], evidence_issues=e.get('evidence_issues')) for e in self.events],
                       counts=dict(self.counts))
        try:
            self.queue.put(('close', message), timeout=max(0, deadline-time.monotonic()))
        except queue.Full:
            self.stop.set()
        while self.thread.is_alive() and time.monotonic() < deadline:
            self._poll_worker()
            self.thread.join(timeout=min(.05, max(0, deadline-time.monotonic())))
        self._poll_worker()
        if self.backend == 'process':
            if self.thread.is_alive():
                self.client_metrics['forced_terminate'] = True
                self.thread.terminate()
                self.thread.join(timeout=.5)
                for event in self.events:
                    if event['status'] == 'queued':
                        event['status'] = 'evidence_flush_timeout'
            self.client_metrics['worker_exitcode'] = self.thread.exitcode
            self.queue.cancel_join_thread()
            self.queue.close()
            # A terminated writer closes its only pipe endpoint, including a
            # partly sent result. Relay cleanup is bounded independently.
            self.relay_thread.join(timeout=.25)
            self._poll_worker()
            self.relay_stop.set()
            self.result_receiver.close()
            self.client_metrics['relay_alive_after_close'] = self.relay_thread.is_alive()
            self.client_metrics['queue_cleanup'] = 'closed'
            if not self.thread.is_alive():
                self.shared.close()
                self.shared.unlink()
                self.client_metrics['shared_memory_unlinked'] = True
            self._cleaned = True
        else:
            self._cleaned = not self.thread.is_alive()
            self.client_metrics['queue_cleanup'] = 'drained' if self._cleaned else 'worker_still_running'
        return self.summary()
