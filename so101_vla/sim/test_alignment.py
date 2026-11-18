"""Alignment math and dashboard endpoints using synthetic images only."""
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import threading
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from alignment_metrics import compare_markers, deny_serial
from monitor_server import MonitorServer


def board():
    return {i: [[x, y], [x+20, y], [x+20, y+20], [x, y+20]]
            for i, (x, y) in enumerate(((100, 80), (400, 80), (400, 300), (100, 300)))}


class AlignmentTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Linux launch lease; run on Jetson with fake commands')
    def test_runner_releases_and_restores_alignment_cameras(self):
        for exit_code in (0, 17):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);live=root/'live';live.mkdir();bin_dir=root/'bin';bin_dir.mkdir()
                home=root/'home';py=home/'venvs/smolvla-py310/bin/python';py.parent.mkdir(parents=True)
                shutil.copy2(Path(__file__).resolve().parents[1]/'live/run.sh',live/'run.sh')
                scripts={bin_dir/'systemctl': '''#!/bin/sh
echo "SYSTEMCTL $*" >> "$ALIGNMENT_TEST_LOG"
case "$*" in
*is-active*so101-camera-alignment.service*) echo active;;
*is-active*astra-v4l2-bridge.service*) echo active;;
*is-active*) echo inactive; exit 3;;
esac
''', bin_dir/'sudo': '''#!/bin/sh
[ "$1" != -v ] || exit 0
[ "$1" != -n ] || shift
exec "$@"
''',py: '''#!/bin/sh
echo "PY_EXEC $*" >> "$ALIGNMENT_TEST_LOG"
case "$*" in *entry.py*) exit "$ALIGNMENT_TEST_EXIT";; esac
'''}
                for path,text in scripts.items():path.write_text(text);path.chmod(0o700)
                log=root/'calls.txt';env={**os.environ,'HOME':str(home),'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],
                    'XDG_RUNTIME_DIR':str(root),'SO101_IN_TMUX':'1','ALIGNMENT_TEST_LOG':str(log),
                    'ALIGNMENT_TEST_EXIT':str(exit_code)}
                result=subprocess.run(['bash',str(live/'run.sh'),'--live'],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,exit_code,result.stderr)
                calls=log.read_text().splitlines()
                stop=next(i for i,s in enumerate(calls) if s=='SYSTEMCTL --user stop so101-camera-alignment.service')
                execute=next(i for i,s in enumerate(calls) if 'PY_EXEC' in s and 'entry.py' in s)
                restore=next(i for i,s in enumerate(calls) if s=='SYSTEMCTL --user start so101-camera-alignment.service')
                self.assertLess(stop,execute);self.assertGreater(restore,execute)

    def test_translation_and_twenty_pixel_boundary(self):
        ref = board()
        for dx, expected in ((20, True), (20.1, False), (188, False)):
            cur = {i: [[x+dx, y] for x,y in points] for i,points in ref.items()}
            got = compare_markers(ref, cur)
            self.assertAlmostEqual(got['position_difference_px'], dx)
            self.assertAlmostEqual(got['rotation_deg'], 0)
            self.assertEqual(got['within_target'], expected)

    def test_rotation_and_scale_are_image_plane_quantities(self):
        ref = board(); angle = math.radians(13)
        cur = {i: [[1.2*(x*math.cos(angle)-y*math.sin(angle))+5,
                    1.2*(x*math.sin(angle)+y*math.cos(angle))-8] for x,y in points]
               for i,points in ref.items()}
        got = compare_markers(ref, cur)
        self.assertAlmostEqual(got['rotation_deg'], 13)
        self.assertAlmostEqual(got['scale'], 1.2)

    def test_moving_bottle_markers_do_not_change_board_alignment(self):
        ref = board();cur = board()
        ref[7] = [[0,0]]*4;cur[7] = [[600,400]]*4
        self.assertEqual(compare_markers(ref, cur)['position_difference_px'], 0)

    def test_insufficient_markers_never_reports_ok(self):
        ref = board();got = compare_markers(ref, {0:ref[0]})
        self.assertIsNone(got['within_target'])
        self.assertIsNone(got['position_difference_px'])
        self.assertEqual(got['status'], 'insufficient_markers')

    def test_serial_open_denied_but_cameras_allowed(self):
        for path in ('/dev/ttyACM0', '/dev/serial/by-id/follower', b'/dev/ttyUSB0'):
            with self.assertRaises(RuntimeError): deny_serial('open', (path, 'rb'))
        deny_serial('open', ('/dev/video8', 'rb'))

    def test_dashboard_stale_frames_hidden_and_reference_allowlist(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);assets=root/'assets';(assets/'alignment').mkdir(parents=True)
            (assets/'alignment/reference.json').write_text(json.dumps({'episode':93}))
            (assets/'alignment/training_first_ceiling_vertical.png').write_bytes(b'png_fixture')
            data=dict(phase='ready',captured=time.time(),images={r:base64.b64encode(b'test').decode()
                       for r in ('end_effector','ceiling_vertical','ceiling_oblique')})
            path=root/'alignment.json';path.write_text(json.dumps(data))
            with patch('monitor_server.ASSETS', assets):
                server=MonitorServer(('127.0.0.1',0),root,'127.0.0.1',root)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base='http://127.0.0.1:'+str(server.server_port)
                try:
                    with urlopen(base+'/api/alignment') as f:got=json.load(f)
                    self.assertTrue(got['fresh']);self.assertEqual(len(got['images']),3)
                    data['captured']-=10;path.write_text(json.dumps(data))
                    with urlopen(base+'/api/alignment') as f:got=json.load(f)
                    self.assertFalse(got['fresh']);self.assertEqual(got['images'],{})
                    with urlopen(base+'/api/alignment/reference/ceiling_vertical.png') as f:
                        self.assertEqual(f.read(),b'png_fixture')
                    with self.assertRaises(HTTPError) as e:urlopen(base+'/api/alignment/reference/other.png')
                    self.assertEqual(e.exception.code,404)
                    e.exception.close()
                finally:
                    server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':unittest.main()
