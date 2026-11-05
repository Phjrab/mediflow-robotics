import io
import unittest
from unittest.mock import patch
import check_lights_via_lelab as lights


class Tests(unittest.TestCase):
    def test_two_jpegs_across_read_boundaries(self):
        first = b'\xff\xd8' + b'x' * 4090 + b'\xff\xd9'
        second = b'\xff\xd8abc\xff\xd9'
        self.assertEqual(lights.jpeg_frames(io.BytesIO(b'--frame\r\n' + first + second)), [first, second])

    def test_truncated_and_oversize_fail(self):
        with self.assertRaises(ValueError):
            lights.jpeg_frames(io.BytesIO(b'\xff\xd8truncated'))
        with self.assertRaises(ValueError):
            lights.jpeg_frames(io.BytesIO(b'x' * 100), max_bytes=10)

    def test_activity_requires_explicit_false(self):
        for value in (True, None, 0, 'false'):
            class Response(io.BytesIO):
                pass
            with patch.object(lights.urllib.request, 'urlopen', return_value=Response(__import__('json').dumps({'recording_active': value}).encode())):
                with self.assertRaises(RuntimeError):
                    lights.states()


if __name__ == '__main__':
    unittest.main()
