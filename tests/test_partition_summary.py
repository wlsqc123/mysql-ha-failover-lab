import unittest

from workload.analyze_partition import summarize


class PartitionSummaryTests(unittest.TestCase):
    def setUp(self):
        def event(kind, second, **extra):
            return {'event': kind, 'time_utc': f'2026-10-22T00:00:{second:02d}+00:00', **extra}
        self.cluster = [event('disconnect_requested', 10, target='mysql-3'),
                        event('disconnect_confirmed', 11),
                        event('snapshot', 16, snapshot={'members': [
                            {'MEMBER_HOST': 'mysql-1', 'MEMBER_ROLE': 'PRIMARY', 'MEMBER_STATE': 'ONLINE'},
                            {'MEMBER_HOST': 'mysql-2', 'MEMBER_ROLE': 'SECONDARY', 'MEMBER_STATE': 'ONLINE'}]}),
                        event('network_restored', 30)]
        self.clients = [event('commit_ack', 17)]
        self.probe = {'outcome': 'UNKNOWN', 'timed_out': True, 'snapshot': {
            'offline_mode': 0, 'read_only': 0, 'super_read_only': 0,
            'members': [{'MEMBER_STATE': 'ONLINE'}, {'MEMBER_STATE': 'UNREACHABLE'}, {'MEMBER_STATE': 'UNREACHABLE'}]}}
        self.verification = {'passed': True, **{k: 0 for k in (
            'missing_acknowledged_requests', 'missing_confirmed_requests', 'duplicate_grants',
            'balance_mismatches', 'payload_mismatches', 'unexpected_grants')}}

    def test_pending_commit_is_not_reported_as_rejected(self):
        report = summarize(self.cluster, self.clients, self.probe, self.verification, 0)
        self.assertTrue(report['passed'])
        self.assertEqual(report['isolated_probe_outcome'], 'UNKNOWN')

    def test_retry_exhaustion_is_failure_despite_integrity(self):
        self.verification.update(passed=False, failed_requests=1)
        report = summarize(self.cluster, self.clients, self.probe, self.verification, 0)
        self.assertFalse(report['passed'])
        self.assertTrue(report['data_integrity_passed'])

    def test_committed_probe_cannot_pass_as_blocked(self):
        self.assertFalse(summarize(self.cluster, self.clients, self.probe, self.verification, 1)['passed'])
