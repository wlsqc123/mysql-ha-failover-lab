import unittest

from workload.observe import assert_restored


class RejoinChecksTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'observer': 'mysql-1', 'read_only': 1, 'applier_errors': [], 'members': [
            {'MEMBER_HOST': 'mysql-1', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'SECONDARY'},
            {'MEMBER_HOST': 'mysql-2', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'SECONDARY'},
            {'MEMBER_HOST': 'mysql-3', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'PRIMARY'}]}

    def test_returning_secondary_is_accepted(self):
        assert_restored(self.snapshot, 'mysql-3')

    def test_recovering_member_is_not_ready(self):
        self.snapshot['members'][0]['MEMBER_STATE'] = 'RECOVERING'
        with self.assertRaises(RuntimeError):
            assert_restored(self.snapshot, 'mysql-3')

    def test_role_reversal_is_not_accepted(self):
        self.snapshot['members'][0]['MEMBER_ROLE'] = 'PRIMARY'
        self.snapshot['members'][2]['MEMBER_ROLE'] = 'SECONDARY'
        with self.assertRaises(RuntimeError):
            assert_restored(self.snapshot, 'mysql-3')

    def test_writable_secondary_is_not_accepted(self):
        self.snapshot['read_only'] = 0
        with self.assertRaises(RuntimeError):
            assert_restored(self.snapshot, 'mysql-3')
