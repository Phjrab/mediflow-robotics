import unittest
from unittest.mock import patch
import check_three_cameras as camera


class Tests(unittest.TestCase):
    def test_initial_frames_discarded_instead_of_reused(self):
        with patch.object(camera.time, 'monotonic', side_effect=[0, 0, 1, 3.1]), \
             patch.object(camera, 'jpeg_frames', side_effect=[[b'dark0', b'dark1'], [b'unstable0', b'unstable1'], [b'stable0', b'stable1']]) as reader:
            self.assertEqual(camera.stable_jpegs(object()), [b'stable0', b'stable1'])
        self.assertEqual(reader.call_count, 3)

    def test_stream_error_propagates(self):
        with patch.object(camera.time, 'monotonic', side_effect=[0, 0]), \
             patch.object(camera, 'jpeg_frames', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                camera.stable_jpegs(object())


if __name__ == '__main__':
    unittest.main()
