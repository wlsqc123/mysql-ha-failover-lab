import unittest

from workload.analyze_failover import summarize


class FailoverSummaryTests(unittest.TestCase):
    def event(self, name, second, **extra):
        return {'event': name, 'time_utc': f'2026-10-22T00:00:{second:02d}+00:00', **extra}

    def test_election_and_write_recovery_are_separate(self):
        cluster = [self.event('kill_requested', 10, target='mysql-1'),
                   self.event('kill_confirmed', 11),
                   self.event('snapshot', 16, snapshot={'members': [
                       {'MEMBER_HOST': 'mysql-2', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'PRIMARY'},
                       {'MEMBER_HOST': 'mysql-3', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'SECONDARY'}]})]
        clients = [self.event('commit_ack', 5), self.event('confirmed_existing', 12), self.event('commit_ack', 18)]
        result = summarize(cluster, clients, {'passed': True})
        self.assertEqual(result['primary_election_observed_seconds'], 6)
        self.assertEqual(result['application_recovery_observed_seconds'], 8)
        self.assertTrue(result['passed'])

    def test_existing_result_alone_does_not_prove_write_recovery(self):
        cluster = [self.event('kill_requested', 10, target='mysql-1'), self.event('kill_confirmed', 11)]
        result = summarize(cluster, [self.event('confirmed_existing', 12)], {'passed': True})
        self.assertIsNone(result['application_recovery_observed_seconds'])
        self.assertFalse(result['passed'])
