"""Bounded field execution of the data-derived position commands."""

from data_motion import Tracking
from first_trial import SupervisedAdapter
from first_trial_core import Hold, frame_gate, home_gate, sensor_gate, vector
from work_session import SessionStages


class DataMotionAdapter(SupervisedAdapter):
    operator_limits_message = "직전 전송 목표 기준 2도·관절별 속도 상한·15도 보류"
    def configure_data_motion(self, rates, initial_raw):
        self.rates = rates
        self.initial_raw = initial_raw
        self.tracking = None
        self.data_steps = []

    def motion_snapshot(self):
        snapshot = self.io.snapshot()
        state = sensor_gate(snapshot, self.limits)
        now = self.clock()
        return snapshot, state, now

    def monitor(self):
        if self.tracking is None:
            return super().monitor()
        if self.stop.is_set():
            raise Hold("정지 요청")
        self.io.check_records()
        _, state, now = self.motion_snapshot()
        self.tracking.observe(state, now)
        self.monitor_count += 1

    def execute_stage(self, stage):
        if stage != "first_vla_motion" or not self.prepared:
            raise Hold("미준비 단계")
        limits = self.limits
        deadline = min(self.clock() + limits.max_seconds, getattr(self, "session_deadline", float("inf")))
        frames = self.await_job(self.io.capture_frames, deadline, 1.)
        self.previous_hash = frame_gate(frames, self.clock(), limits)
        state = sensor_gate(self.io.snapshot(), limits, starting=True)
        if not getattr(self, "skip_home_recheck", False):
            home_gate(state, self.home)
        if self.tracking is None:
            self.tracking = Tracking(self.io.encoder_axes(), self.rates, self.initial_raw, state, self.clock())
        else:
            self.tracking.begin_chunk()
            self.tracking.observe(state, self.clock())
        started = self.clock()
        actions = self.await_job(lambda: self.io.predict(frames, state), deadline, 50/limits.fps)
        self.policy_chunk = dict(raw_actions=actions, observation_state=state, bounds=self.tracking.bounds,
                                 inference_seconds=self.clock()-started, fps=limits.fps)
        if self.clock() - min(row["host_received"] for row in frames.values()) > 50/limits.fps:
            raise Hold("정책 입력 나이/추론 지연 초과")
        if len(actions) != 50:
            raise Hold("행동50개 필요")
        for action in actions:
            vector(action)
        cadence = 1 / limits.fps
        self.step_timings = []
        for step, action in enumerate(actions[:limits.max_steps]):
            # A held policy row is retried without transmitting or advancing the index.
            while True:
                if self.stop.is_set() or self.clock() >= deadline:
                    raise Hold("시간 상한/정지 요청")
                if step % 5 == 0 or self.clock()-min(row["host_received"] for row in frames.values()) > limits.frame_age:
                    frames = self.await_job(self.io.capture_frames, deadline, 1.)
                    self.previous_hash = frame_gate(frames, self.clock(), limits, self.previous_hash)
                last = self.tracking.last_sent
                if last is not None:
                    self.sleep(max(0., last + cadence - self.clock()))
                self.io.check_records()
                snapshot, measured, now = self.motion_snapshot()
                row = dict(step=len(self.sent), policy_index=step, time=now, raw_action=list(action),
                           measured_state=measured, loads=snapshot["loads"], currents=snapshot["currents"],
                           sdk_wire_raw=None, status="INSPECTED")
                self.data_steps.append(row)
                gaps = self.tracking.observe(measured, now)
                row.update(self.tracking.propose(action, measured, gaps, now))
                if row["status"] == "HELD":
                    if len(self.data_steps) == 1 or self.data_steps[-2]["status"] != "HELD":
                        print("추종 차이 15° 초과. 새 목표 보류", row["warning_joints"], flush=True)
                    self.sleep(cadence)
                    continue
                def before_wire():
                    current = self.clock()
                    if self.stop.is_set() or current >= deadline or current-now > cadence:
                        raise Hold("전송 직전 정지/시간/관측 지연 상한")
                before_wire()
                raw = row["planned_wire_raw"]
                sent_at = self.clock()
                wire = self.io.send_encoded_target(row["target"], raw, before_wire)
                row.update(sdk_wire_raw=wire["wire_raw"], sdk_wire_normalized=wire["wire_normalized"], status="SENT")
                self.tracking.sent(wire["wire_raw"], self.clock())
                self.sent.append(dict(step=step, raw_action=list(action), target=row["target"], state=measured,
                                      clip=any(row["clamp_reasons"].values()), sent_at=sent_at))
                self.step_timings.append(dict(step=step, sent_at=sent_at, state_age_seconds=sent_at-now))
                break
        # Allow one response window, with health checks and no additional commands.
        until = self.clock() + .5
        while self.clock() < until:
            if self.clock() >= deadline:
                raise Hold("최종 추종 관측 시간 상한")
            self.monitor()
            self.sleep(min(cadence, max(0., until-self.clock())))
        if self.clock() >= deadline:
            raise Hold("최종 추종 관측 시간 상한")
        self.monitor()
        self.events.append("data_motion_segment_complete")


class DataMotionSession(SessionStages, DataMotionAdapter):
    session_max_steps = 300
    session_max_seconds = 30
    data_session = True
