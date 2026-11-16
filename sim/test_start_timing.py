"""Full stream regressions for startup and an inference stall; no hardware."""
import unittest
from config_runtime import load
from startup_simulation import SimulatedArm
from stream_live import stream,chunk_alignment


def bursty_run(first_slow=False):
    home=list(load()['home']['state']);io=SimulatedArm(home,{},lambda _:None,'camera_dry')
    io.policy_active=True;calls=0;rows=[]
    def policy(frames,state,origin,delay):
        nonlocal calls
        calls+=1;actions=[]
        for index in range(50):
            target=home.copy();target[2]-=(origin+index)*.03;actions.append(target)
        slow=calls==1 if first_slow else calls in (2,3)
        return actions,5.8 if slow else .05
    return stream(io,policy,rows.append,25.,.33),rows


class StartTimingTests(unittest.TestCase):
    def test_frozen_playback_does_not_consume_wall_clock_actions(self):
        expired,usable=chunk_alignment(35,5.8,9.9,50,moving=True)
        self.assertEqual((expired,usable),(15,50))
        self.assertLess(expired,50)

    def test_real_stream_keeps_usable_chunk_after_two_slow_inferences(self):
        result,rows=bursty_run()
        self.assertEqual(result['status'],'RUN_COMPLETE')
        inference=[r for r in rows if r['event']=='inference']
        self.assertFalse(any(r['discarded'] for r in inference))
        self.assertTrue(any(r['wall_time_expired_actions']>r['expired_actions'] for r in inference))
        self.assertLess(result['chunk_wait_s']['max'],5.)
        phases=[r['model_phase'] for r in rows if r['event']=='goal_transmitted']
        self.assertEqual(phases,sorted(phases))
        self.assertGreater(result['steps'],700)

    def test_long_initial_inference_starts_at_action_zero(self):
        result,rows=bursty_run(first_slow=True)
        self.assertEqual(result['status'],'RUN_COMPLETE')
        inference=next(r for r in rows if r['event']=='inference')
        self.assertEqual(inference['skipped_actions'],0)
        first=next(r for r in rows if r['event']=='goal_transmitted')
        self.assertEqual(first['model_phase'],0.)
        self.assertAlmostEqual(first['model'][2],load()['home']['state'][2])


if __name__=='__main__':unittest.main()
