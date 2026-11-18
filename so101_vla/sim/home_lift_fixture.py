"""Virtual shoulder response based on the 16:18 live log.

The optional movement beyond the recorded cutoff is a test scenario, not a measurement.
"""
from startup_simulation import SimulatedArm
from start_position_rules import home_targets


class LoadedLiftArm(SimulatedArm):
    def __init__(self, *args, late_progress=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.present = {**home_targets(), 'shoulder_lift': 858, 'elbow_flex': 3085}
        self.target = dict(self.present)
        self.late_progress = late_progress
        self.last_lift_send_at = None

    def send(self, raw):
        super().send(raw)
        self.last_lift_send_at = self.clock()

    def feedback(self):
        sample = super().feedback()
        if self.last_lift_send_at is None or getattr(self, 'motion_ready', False):
            return sample
        goal = self.target['shoulder_lift']
        elapsed = self.clock()-self.last_lift_send_at
        if goal >= 834:
            lift = round(854+(goal-834)/4)
        elif goal > 826:
            lift = round(851+3*(goal-826)/8)
        elif goal == 826:
            # The real position stayed at 851 for about 3.1 s, then reached 850.
            lift = 851 if elapsed < 3.10 else 850
            if self.late_progress and elapsed >= 3.75:
                lift = 849 if elapsed < 4.5 else 848
        else:
            raise AssertionError('Reference home fixture received a goal below 826 ticks')
        self.present['shoulder_lift'] = lift
        sample['present_raw']['shoulder_lift'] = lift
        sample['state'] = self.normalize(sample['present_raw'])
        sample['loads']['shoulder_lift'] = max(0, (lift-goal)*4+12)
        return sample
