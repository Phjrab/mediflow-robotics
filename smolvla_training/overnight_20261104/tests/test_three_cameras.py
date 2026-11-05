import unittest
from check_three_cameras import allowed_owners


class Tests(unittest.TestCase):
    def test_normal_camera_requires_empty_successful_query(self):
        self.assertTrue(allowed_owners(1, '', ''))
        self.assertFalse(allowed_owners(1, '', 'sudo: a password is required'))
        self.assertFalse(allowed_owners(0, '123', ''))

    def test_astra_requires_only_known_producer_group(self):
        def group(pid):
            return '0::/system.slice/astra-v4l2-bridge.service' if pid == '12' else '0::/user.slice/lelab.service'
        self.assertTrue(allowed_owners(0, '12', '/dev/video8:', True, group))
        self.assertFalse(allowed_owners(0, '12 34', '/dev/video8:', True, group))
        self.assertFalse(allowed_owners(1, '', '', True, group))

    def test_unreadable_producer_and_query_errors_fail(self):
        def error(pid):
            raise OSError('unreadable')
        self.assertFalse(allowed_owners(0, '12', '', True, error))
        self.assertFalse(allowed_owners(0, '12', 'Permission denied', True, lambda pid: '0::/astra-v4l2-bridge.service'))


if __name__ == '__main__':
    unittest.main()
