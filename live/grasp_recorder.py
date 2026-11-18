"""Nonblocking, device-free evidence recording around transmitted gripper closes.

Increasing gripper points means opening on this calibrated robot. This recorder
observes successful goals; it never opens a device or changes a command.
"""
import copy
import io
import json
import math
import queue
import threading
import time
from collections import deque
from pathlib import Path


CAMERAS = ('end_effector', 'ceiling_vertical', 'ceiling_oblique')


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

    Frames are copied at at most 10 Hz, encoded once in a bounded worker queue,
    and retained as JPEG bytes for five seconds. On an evidence I/O failure the
    control loop continues and the summary explicitly reports lost evidence.
    """
    def __init__(self, output, joints, *, fps=10, ring_seconds=5, pre_seconds=3,
                 post_seconds=2, close_points=3, max_queue=256, max_pending_frames=8,
                 frame_max_age=.5, encoder=jpeg_rgb, clock=time.monotonic,
                 wall_clock=time.time, cameras=CAMERAS):
        if min(fps, ring_seconds, pre_seconds, post_seconds, close_points) <= 0:
            raise ValueError('recording durations, rate, and threshold must be positive')
        if ring_seconds < pre_seconds or max_queue < 1 or max_pending_frames < 1:
            raise ValueError('ring must cover the pre-event window and queue must be bounded')
        self.output, self.joints = Path(output), tuple(joints)
        self.fps, self.ring_seconds = fps, ring_seconds
        self.pre_seconds, self.post_seconds, self.close_points = pre_seconds, post_seconds, close_points
        self.frame_max_age, self.encoder = frame_max_age, encoder
        self.clock, self.wall_clock, self.cameras = clock, wall_clock, tuple(cameras)
        self.queue = queue.Queue(maxsize=max_queue)
        self.max_pending_frames = max_pending_frames
        self.pending_frames = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
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
        self.thread = threading.Thread(target=self._worker, name='vla-grasp-evidence', daemon=True)
        self.thread.start()

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
        try:
            self.queue.put_nowait((kind, data))
            return True
        except queue.Full:
            self._increment({'frame': 'dropped_frames', 'event': 'dropped_events',
                             'goal': 'dropped_telemetry'}[kind])
            return False

    def capture(self, frames, sample, *, time_s, monotonic=None, unix=None):
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
        images, sources = {}, {}
        for name in self.cameras:
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
            images[name] = pixels.copy()
            sources[name] = dict(camera_monotonic_s=captured,
                                 age_s=None if captured is None else now-captured,
                                 timestamp_verified=captured is not None)
            self.previous_frames[name] = token
        if not images:
            return False
        data = dict(time_s=time_s, monotonic_s=now,
                    unix=self.wall_clock() if unix is None else unix,
                    feedback=feedback_fields(sample), previous_goal=copy.deepcopy(self.latest_goal),
                    sources=sources, pixels=images)
        with self.lock:
            self.pending_frames += 1
        if not self._enqueue('frame', data):
            with self.lock:
                self.pending_frames -= 1
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

    def _finish_event(self, active, *, partial, reason):
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

    def _worker(self):
        ring, active = deque(), []
        try:
            while not (self.stop.is_set() and self.queue.empty()):
                try:
                    kind, data = self.queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    now_s = data['event']['time_s'] if kind == 'event' else data['time_s']
                    for item in tuple(active):
                        if now_s > item['event']['window_end_s']+1e-8:
                            self._finish_event(item, partial=False, reason='post_window_complete')
                            active.remove(item)
                    if kind == 'frame':
                        batch = {key: value for key, value in data.items() if key != 'pixels'}
                        batch['jpeg'] = {name: self.encoder(pixels) for name, pixels in data['pixels'].items()}
                        self._increment('encoded_images', len(batch['jpeg']))
                        ring.append(batch)
                        while ring and ring[0]['time_s'] < now_s-self.ring_seconds:
                            ring.popleft()
                        for item in active:
                            self._save_batch(item, batch)
                    elif kind == 'event':
                        event = data['event']
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
                        with self.lock:
                            self.pending_frames -= 1
                    self.queue.task_done()
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

    def summary(self):
        with self.lock:
            events = [{key: copy.deepcopy(event.get(key)) for key in
                       ('id', 'directory', 'time_s', 'unix', 'status', 'frames', 'goals',
                        'trigger_drop_points')} for event in self.events]
            return dict(event_count=len(events), events=events, counts=dict(self.counts),
                        errors=list(self.errors), fps=self.fps, ring_seconds=self.ring_seconds,
                        pre_seconds=self.pre_seconds, post_seconds=self.post_seconds,
                        writer_alive=self.thread.is_alive())

    def close(self, *, reason='run_end', timeout=10):
        self.closed, self.end_reason = True, reason
        self.stop.set()
        self.thread.join(timeout=timeout)
        return self.summary()
