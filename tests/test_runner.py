import unittest
from unittest.mock import patch

import mysql.connector

from workload.client import RequestMismatch, UnknownOutcome
from workload.runner import process_request


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.request = {'request_id': 'retry-test', 'account_id': 1, 'amount': 100}
        self.events = []

    @patch('workload.runner.grant_reward')
    def test_connection_error_retries_same_payload(self, grant):
        grant.side_effect = [mysql.connector.OperationalError('disconnected', errno=2013), 'GRANTED']
        self.assertEqual(process_request(self.request, self.events.append, 3, 0), 'GRANTED')
        self.assertEqual(grant.call_args_list[0], grant.call_args_list[1])
        self.assertEqual(self.events[-1]['event'], 'commit_ack')

    @patch('workload.runner.grant_reward')
    def test_unknown_commit_resolved_by_existing(self, grant):
        grant.side_effect = [UnknownOutcome('response lost'), 'ALREADY_GRANTED']
        self.assertEqual(process_request(self.request, self.events.append, 3, 0), 'ALREADY_GRANTED')
        self.assertEqual(self.events[-1]['event'], 'confirmed_existing')
        self.assertNotIn('commit_ack', [e['event'] for e in self.events])

    @patch('workload.runner.grant_reward')
    def test_unknown_stays_unknown_after_exhaustion(self, grant):
        grant.side_effect = [UnknownOutcome('response lost'), mysql.connector.OperationalError('disconnected', errno=2013)]
        self.assertEqual(process_request(self.request, self.events.append, 2, 0), 'UNKNOWN')

    @patch('workload.runner.grant_reward')
    def test_mismatch_not_retried(self, grant):
        grant.side_effect = RequestMismatch('different amount')
        self.assertEqual(process_request(self.request, self.events.append, 5, 0), 'FAILED')
        self.assertEqual(grant.call_count, 1)

    @patch('workload.runner.grant_reward')
    def test_auth_error_not_retried(self, grant):
        grant.side_effect = mysql.connector.ProgrammingError('access denied', errno=1045)
        self.assertEqual(process_request(self.request, self.events.append, 5, 0), 'FAILED')
        self.assertEqual(grant.call_count, 1)
