import unittest

from workload.isolated_probe import classify


class ProbeClassificationTests(unittest.TestCase):
    def test_timeout_is_unknown(self):
        self.assertEqual(classify([{'event': 'commit_started'}], True), 'UNKNOWN')

    def test_offline_rejection_is_separate(self):
        self.assertEqual(classify([{'event': 'db_error', 'errno': 3032}], False), 'WRITE_REJECTED')

    def test_transport_error_is_not_quorum_evidence(self):
        self.assertEqual(classify([{'event': 'db_error', 'errno': 2003}], False), 'INCONCLUSIVE')

    def test_ack_is_not_hidden_by_timeout(self):
        self.assertEqual(classify([{'event': 'commit_ack'}], True), 'ACKNOWLEDGED')
