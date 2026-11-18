import contextlib
import io
import unittest

from loop_timing import LoopTiming, terminal_lines
from config_runtime import load
from startup_simulation import SimulatedArm
from stream_live import stream


class Clock:
    def __init__(self): self.now = 0.
    def clock(self): return self.now
    def pause(self, value): self.now += value


class LoopTimingTests(unittest.TestCase):
    def test_partial_window_progress_and_inference_are_not_additive_work(self):
        clock=Clock();rows=[];timer=LoopTiming(0., .33, rows.append, clock=clock.clock)
        for i in range(76):
            clock.now=i*.02
            tick=timer.begin()
            tick.call('serial_read', clock.pause, .002)
            tick.call('serial_write', clock.pause, .001)
            tick.finish(phase=clock.now*9.9)
        timer.inference(1.5, at=.8)
        result=timer.summary(clock.now)
        first,partial=result['windows']
        self.assertAlmostEqual(first['control_hz']['p5'], 50)
        self.assertAlmostEqual(first['action_progress_ratio'], 1.)
        self.assertFalse(first['lag'])
        self.assertEqual(first['inference_ms']['mean'], 1500.)
        self.assertEqual(first['measured_work_ms']['inference_blocking'],0.)
        self.assertAlmostEqual(partial['duration_s'],.503)
        self.assertAlmostEqual(partial['action_progress_ratio'],1.)
        self.assertIn('타이밍', terminal_lines(result)[0])

    def test_stalled_timeline_and_low_p5_are_explicit(self):
        clock=Clock();timer=LoopTiming(0.,.33,lambda row:None,clock=clock.clock)
        for index in range(20):
            clock.now=index*.05
            tick=timer.begin()
            tick.call('serial_read',clock.pause,.04)
            tick.action_starved=True
            tick.finish(phase=0.)
        row=timer.summary(clock.now)['windows'][0]
        self.assertTrue(row['lag']);self.assertEqual(row['action_progress_ratio'],0.)
        self.assertAlmostEqual(row['control_hz']['p5'],20.)
        self.assertIn('largest_measured_work:serial_read',row['possible_causes'])
        self.assertIn('action_buffer_empty',row['possible_causes'])

    def test_readonly_does_not_invent_inference_or_policy_progress(self):
        clock=Clock();timer=LoopTiming(0.,.33,lambda row:None,clock=clock.clock,policy_timeline=False)
        tick=timer.begin();clock.pause(.01);tick.finish()
        row=timer.summary(clock.now)['windows'][0]
        self.assertIsNone(row['action_progress_ratio'])
        self.assertIsNone(row['inference_ms']['mean'])
        self.assertIsNone(row['control_hz']['mean'])

    def test_stream_logs_failed_read_and_holds_before_terminal_summary(self):
        home=list(load()['home']['state']);arm=SimulatedArm(home,{},lambda row:None,'camera_dry')
        arm.policy_active=True;checked=arm.checked;count=[0];rows=[]
        def read():
            count[0]+=1
            if count[0]>8:
                arm.pause(.17)
                raise RuntimeError('fixture bad ID')
            return checked()
        arm.checked=read
        with contextlib.redirect_stdout(io.StringIO()):
            result=stream(arm,lambda *args:([home]*50,.01),rows.append,.5,.33)
        loops=[row for row in rows if row.get('event')=='loop_timing']
        self.assertTrue(loops)
        self.assertTrue(loops[-1]['failed'])
        self.assertGreaterEqual(loops[-1]['read_ms'],169.9)
        self.assertEqual(result['status'],'HOLD')
        self.assertIn('loop_timing',result)


if __name__=='__main__': unittest.main()
