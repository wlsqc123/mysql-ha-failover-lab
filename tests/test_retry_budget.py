import unittest
from unittest.mock import patch

import mysql.connector

from workload.client import UnknownOutcome
from workload.reprocess import select_pending
from workload.runner import process_request


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.request = {'request_id': 'budget-test', 'account_id': 1, 'amount': 100}
        self.events = []
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def test_budget_limits_new_attempts_and_caps_wait(self):
        with patch('workload.runner.time.monotonic', side_effect=lambda: self.now), \
             patch('workload.runner.time.sleep', side_effect=self.sleep) as sleeper, \
             patch('workload.runner.grant_reward', side_effect=mysql.connector.OperationalError('offline', errno=2003)) as grant:
            result = process_request(self.request, self.events.append, retry_budget=1, retry_delay=0.7)
        self.assertEqual(result, 'FAILED')
        self.assertEqual(grant.call_count, 2)
        self.assertAlmostEqual(sleeper.call_args.args[0], 0.3)
        self.assertEqual(self.events[-1]['reason'], 'retry_budget_exhausted')

    def test_unknown_is_preserved_when_budget_expires(self):
        with patch('workload.runner.time.monotonic', side_effect=lambda: self.now), \
             patch('workload.runner.time.sleep', side_effect=self.sleep), \
             patch('workload.runner.grant_reward', side_effect=UnknownOutcome('response lost')):
            result = process_request(self.request, self.events.append, retry_budget=1, retry_delay=1)
        self.assertEqual(result, 'UNKNOWN')

    def test_time_budget_can_continue_beyond_old_attempt_limit(self):
        failures = [mysql.connector.OperationalError('offline', errno=2003) for _ in range(45)]
        with patch('workload.runner.time.monotonic', side_effect=lambda: self.now), \
             patch('workload.runner.time.sleep', side_effect=self.sleep), \
             patch('workload.runner.grant_reward', side_effect=failures + ['GRANTED']) as grant:
            result = process_request(self.request, self.events.append, retry_budget=60, retry_delay=0.5)
        self.assertEqual(result, 'GRANTED')
        self.assertEqual(grant.call_count, 46)
        self.assertTrue(all(call.kwargs == self.request for call in grant.call_args_list))

    def test_no_new_attempt_after_slow_call_consumes_budget(self):
        def fail(**request):
            self.now += 2
            raise mysql.connector.OperationalError('offline', errno=2003)
        with patch('workload.runner.time.monotonic', side_effect=lambda: self.now), \
             patch('workload.runner.grant_reward', side_effect=fail) as grant:
            self.assertEqual(process_request(self.request, self.events.append, retry_budget=1), 'FAILED')
        self.assertEqual(grant.call_count, 1)

    def test_pending_selection_preserves_original_payload(self):
        other = {**self.request, 'request_id': 'confirmed'}
        manifest = {'requests': [self.request, other]}
        events = [{'event': 'terminal', 'request_id': self.request['request_id'], 'outcome': 'UNKNOWN'},
                  {'event': 'confirmed_existing', 'request_id': other['request_id']}]
        self.assertEqual(select_pending(manifest, events), [self.request])

    def test_invalid_budget_is_rejected(self):
        for budget in (0, -1, float('nan'), float('inf')):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                process_request(self.request, self.events.append, retry_budget=budget)
