"""Shared legacy/raw policy close gate. Pure Python, no device dependencies.

Replay API: points = gate.apply(model, present, now, policy_knot=raw_upper).
Use consecutive goal_transmitted.model and feedback.state vectors in joint order,
and a monotonic clock in seconds (READY-relative time_s is suitable for replay).
model is the selected policy action (interpolated/blended in legacy, FIFO in raw),
before all lateral, relief, compensation, quantization and transmission transforms.
For legacy, policy_knot is the newest accepted unblended row at ceil(model_phase).
For raw, omit it: model is that exact FIFO row. The gripper request is always
model[-1]. The returned position is in normalized opening points. Larger means open.
Call finish(now) at session end and summary() for decisions and wait totals.
"""
import math


AXES = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex')
TOLERANCE_DEG = 3.
TIMEOUT_S = 1.5


def gripper_ticks(plan):
    raw = plan['wire_raw']
    return raw.get('gripper') if isinstance(raw, dict) else None


def record_gripper_packet(tick, log, recorder, row, sent):
    """Use the actual send completion for both the trace and grasp recorder."""
    log(row)
    if recorder is not None:
        grasp = tick.call('recorder_enqueue', recorder.goal, row['wire_normalized'][-1], row,
                          time_s=row['time_s'], monotonic=sent)
        if grasp is not None:
            log(dict(event='grasp_event', **grasp))


class GripperArrivalGate:
    def __init__(self, log=None):
        self.log = log
        self.started = self.held = None
        self.held_ticks = None
        self.released_low = None
        self.reopening_peak = None
        self.last_observation = None
        self.events = []

    def _event(self, kind, now):
        row = dict(event='gripper_close_wait', decision=kind, time_s=now,
                   wait_s=now-self.started, held_points=self.held, held_ticks=self.held_ticks,
                   **self.last_observation)
        self.events.append(row)
        if self.log is not None:
            self.log(row.copy())

    def _end(self, kind, now):
        self._event(kind, now)
        self.started = self.held = self.held_ticks = None

    def apply(self, model, present, now, *, policy_knot=None, knot_index=None, knot_origin=None,
              requested_ticks=None, present_ticks=None):
        knot = model if policy_knot is None else policy_knot
        if (len(knot) != 6 or len(model) != 6 or len(present) != 6 or
                not all(math.isfinite(v) for v in (*knot, *model, *present, now))):
            raise ValueError('close gate requires finite policy/state vectors and clock')
        requested, measured = model[-1], present[-1]
        use_ticks = requested_ticks is not None and present_ticks is not None
        command, position = (requested_ticks, present_ticks) if use_ticks else (requested, measured)
        if self.released_low is not None:
            # Authorize one continuous closing episode (including a plateau).
            # Rearm on a real opening demand or a renewed descent after a rebound.
            if command > self.released_low:
                self.reopening_peak = command if self.reopening_peak is None else max(command, self.reopening_peak)
            reopened = (self.reopening_peak is not None and
                        (command >= position or command < self.reopening_peak))
            if not reopened:
                self.released_low = min(self.released_low, command)
                return requested
            self.released_low = self.reopening_peak = None
        closing = command < position
        if self.started is None and not closing:
            return requested
        errors = {joint: knot[i]-present[i] for i, joint in enumerate(AXES)}
        arrived = max(map(abs, errors.values())) <= TOLERANCE_DEG
        self.last_observation = dict(policy_points=requested, present_points=measured, observed_s=now,
                                     requested_ticks=requested_ticks, present_ticks=present_ticks,
                                     knot_index=knot_index, knot_origin=knot_origin, knot_points=knot[-1],
                                     policy_minus_present_deg=errors,
                                     policy_deg=list(knot[:4]), selected_deg=list(model[:4]),
                                     present_deg=list(present[:4]))
        if self.started is None:
            if arrived:
                # The next policy knot gets its own arrival check. Only a timeout
                # authorizes continuing an off-target close without a new wait.
                return requested
            self.started, self.held = now, measured
            self.held_ticks = present_ticks if use_ticks else None
            self._event('started', now)
        # Drifting feedback cannot withdraw an unchanged policy request.
        withdrawn = requested >= self.held
        if use_ticks and self.held_ticks is not None:
            withdrawn = withdrawn and command >= self.held_ticks
        if withdrawn:
            self._end('cancelled', now)
            return requested
        if arrived or now-self.started >= TIMEOUT_S:
            self._end('arrived' if arrived else 'timeout', now)
            if not arrived:
                self.released_low = command
            return requested
        return self.held

    def preserve_held_tick(self, plan, protected_points, bounds):
        """Avoid a second SDK quantization of measured ticks; all safety caps win."""
        if (self.held_ticks is not None and protected_points == self.held and bounds is not None
                and bounds[0] <= self.held_ticks <= bounds[1]
                and plan.get('bounded', [protected_points])[-1] == protected_points):
            plan['wire_raw']['gripper'] = self.held_ticks
            plan['wire_normalized'][-1] = self.held

    def finish(self, now):
        if self.started is not None:
            self._end('session_end', now)

    def summary(self):
        ends = [r for r in self.events if r['decision'] != 'started']
        return dict(axes=list(AXES), tolerance_deg=TOLERANCE_DEG, timeout_s=TIMEOUT_S,
                    close_basis='encoded policy gripper tick below measured tick; points if ticks unavailable',
                    waits=sum(r['decision'] == 'started' for r in self.events),
                    outcomes={kind: sum(r['decision'] == kind for r in ends)
                              for kind in ('arrived', 'timeout', 'cancelled', 'session_end')},
                    total_wait_s=sum(r['wait_s'] for r in ends),
                    max_wait_s=max((r['wait_s'] for r in ends), default=0.),
                    events=list(self.events))
