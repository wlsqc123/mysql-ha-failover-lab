import unittest

from workload.analyze_failover import summarize_secondary


class SecondarySummaryTests(unittest.TestCase):
    def event(self, kind, second, **extra):
        return {'event': kind, 'time_utc': f'2026-10-22T00:00:{second:02d}+00:00', **extra}

    def cluster(self, changed=False):
        return [self.event('baseline', 0, initial_primary='mysql-3'),
                self.event('kill_requested', 10, target='mysql-1'),
                self.event('kill_confirmed', 11),
                self.event('snapshot', 17, snapshot={'members': [
                    {'MEMBER_HOST': 'mysql-3', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'SECONDARY' if changed else 'PRIMARY'},
                    {'MEMBER_HOST': 'mysql-2', 'MEMBER_STATE': 'ONLINE', 'MEMBER_ROLE': 'PRIMARY' if changed else 'SECONDARY'}]})]

    def clients(self):
        return [self.event('attempt', 1, request_id='before'), self.event('commit_ack', 2, request_id='before'),
                self.event('attempt', 12, request_id='after'), self.event('commit_ack', 14, request_id='after')]

    def test_secondary_loss_preserves_primary_and_writes(self):
        result = summarize_secondary(self.cluster(), self.clients(), {'passed': True})
        self.assertTrue(result['passed'])
        self.assertEqual(result['failure_detection_observed_seconds'], 7)
        self.assertEqual(result['after_fault_confirmation']['p95_latency_ms'], 2000)

    def test_changed_primary_fails(self):
        result = summarize_secondary(self.cluster(changed=True), self.clients(), {'passed': True})
        self.assertFalse(result['passed'])

    def test_no_new_write_fails(self):
        result = summarize_secondary(self.cluster(), self.clients()[:2], {'passed': True})
        self.assertFalse(result['passed'])
