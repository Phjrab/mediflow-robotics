"""Real stream path with thermal reply fixtures and a virtual motor bus."""
import unittest

import direct_live as base
from config_runtime import load
from individual_reply_guard import StrictIndividualReplies
from startup_simulation import SimulatedArm
from stream_live import stream
from thermal_read import read_temperatures
import test_thermal_diagnostics as fixtures
from elbow_settings_readonly import inspect_read


class ControlRevisionTests(unittest.TestCase):
    def run_stream(self, case):
        io = SimulatedArm(load()['home']['state'], {}, lambda _: None, 'camera_dry')
        io.policy_active = True
        original = io.checked
        samples = 0

        def checked():
            nonlocal samples
            sample = original()
            samples += 1
            values = {i: [34] for i in range(1, 7)}
            if samples == 15 and case == 'spike':
                values[2] = [66, 32, 33]
            if samples >= 25 and case == 'heating':
                temperature = min(60, 50+(samples-25))
                values[2] = [temperature]*3
            bus = fixtures.ExistingReadIntegrationTests().bus(values, corrupt=(case == 'checksum' and samples == 20))
            sample['temperatures'], _ = read_temperatures(bus, dict(zip(base.JOINTS, range(1, 7))),
                                                         dict.fromkeys(base.JOINTS, 34), StrictIndividualReplies)
            base.feedback_gate(sample)
            return sample

        io.checked = checked

        def policy(frames, state, origin, delay):
            target = list(load()['home']['state'])
            target[2] -= 8
            return [target[:] for _ in range(50)], .05

        records = []
        return io, stream(io, policy, records.append, 2., .33), records

    def test_single_spike_reaches_end_through_real_stream(self):
        io, result, rows = self.run_stream('spike')
        self.assertEqual(result['status'], 'RUN_COMPLETE')
        self.assertGreater(result['steps'], 40)
        self.assertEqual(result['tracking_compensation']['enabled_axes'], ['elbow_flex'])
        for row in rows:
            if row['event'] == 'goal_transmitted':
                comp = row['tracking_compensation']
                self.assertLessEqual(abs(comp['wire_offset_deg'][2]), 3)
                for joint in base.JOINTS:
                    if joint != 'elbow_flex':
                        self.assertEqual(row['wire_raw'][joint], comp['uncompensated_raw'][joint])
                for joint, value in row['wire_raw'].items():
                    low, high = load()['limits'][joint]
                    self.assertTrue(low <= value <= high)

    def test_sustained_heating_holds_after_some_goals(self):
        _, result, _ = self.run_stream('heating')
        self.assertEqual(result['status'], 'HOLD')
        self.assertGreater(result['steps'], 0)
        self.assertIn('temperature_c', result['reason'])
        self.assertEqual(result['hold']['status'], 'CURRENT_POSITION_GOAL_TRANSMITTED')

    def test_corrupt_confirmation_still_stops(self):
        _, result, _ = self.run_stream('checksum')
        self.assertEqual(result['status'], 'HOLD')
        self.assertGreater(result['steps'], 0)

    def test_readonly_probe_never_accepts_write_or_other_id(self):
        for opcode, mid, address in ((3, 3, 26), (2, 2, 26), (2, 3, 42)):
            packet = bytearray((255, 255, mid, 4, opcode, address, 1))
            packet.append((~sum(packet[2:])) & 255)
            with self.assertRaises(RuntimeError):
                inspect_read(packet, {(26, 1), (27, 1)})


if __name__ == '__main__':
    unittest.main()
