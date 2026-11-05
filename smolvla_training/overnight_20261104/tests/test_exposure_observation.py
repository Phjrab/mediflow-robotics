import io
import unittest
from observe_realsense_exposure import frames


class Tests(unittest.TestCase):
    def test_jpeg_cross_chunk_boundary(self):
        first = b'\xff\xd8' + b'x' * 4090 + b'\xff\xd9'
        second = b'\xff\xd8abc\xff\xd9'
        iterator = frames(io.BytesIO(b'--frame\r\n' + first + second))
        self.assertEqual(next(iterator), first)
        self.assertEqual(next(iterator), second)

    def test_truncated_stream_rejected(self):
        with self.assertRaises(ValueError):
            next(frames(io.BytesIO(b'\xff\xd8unfinished')))

    def test_bytes_limit(self):
        with self.assertRaises(ValueError):
            next(frames(io.BytesIO(b'x' * 100), max_bytes=10))


if __name__ == '__main__':
    unittest.main()
