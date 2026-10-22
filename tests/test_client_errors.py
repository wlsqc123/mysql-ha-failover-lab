import unittest
from unittest.mock import patch

import mysql.connector

from workload.client import UnknownOutcome, grant_reward


class ClientCleanupTests(unittest.TestCase):
    @patch('workload.client.connect')
    def test_cleanup_failure_does_not_mask_unknown_commit(self, connect):
        conn = connect.return_value
        conn.cursor.return_value.__enter__.return_value.rowcount = 1
        conn.commit.side_effect = mysql.connector.InterfaceError('response lost', errno=2013)
        conn.rollback.side_effect = mysql.connector.InterfaceError('disconnected', errno=2013)
        conn.close.side_effect = mysql.connector.InterfaceError('disconnected', errno=2013)
        with self.assertRaises(UnknownOutcome):
            grant_reward('cleanup-test', 1, 100)
