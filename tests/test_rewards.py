"""Integration tests against the running lab through Router; preserve test records."""
from concurrent.futures import ThreadPoolExecutor
import secrets
import unittest
from uuid import uuid4

import mysql.connector

from workload.client import MAX_BIGINT, RequestMismatch, connect, grant_reward


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.account = secrets.randbelow(MAX_BIGINT - 1) + 1
        self.other_account = secrets.randbelow(MAX_BIGINT - 1) + 1
        self.request = uuid4().hex
        with connect(admin=True) as conn:
            with conn.cursor() as cursor:
                cursor.executemany('INSERT INTO accounts(account_id) VALUES (%s)', [(self.account,), (self.other_account,)])
            conn.commit()

    def state(self):
        with connect(admin=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT balance FROM accounts WHERE account_id=%s', (self.account,))
                balance = cursor.fetchone()[0]
                cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM reward_grants WHERE account_id=%s', (self.account,))
                count, total = cursor.fetchone()
                return balance, count, int(total)

    def test_new_request(self):
        self.assertEqual(grant_reward(self.request, self.account, 100), 'GRANTED')
        self.assertEqual(self.state(), (100, 1, 100))

    def test_repeated_request(self):
        grant_reward(self.request, self.account, 100)
        self.assertEqual(grant_reward(self.request, self.account, 100), 'ALREADY_GRANTED')
        self.assertEqual(self.state(), (100, 1, 100))

    def test_mismatched_amount(self):
        grant_reward(self.request, self.account, 100)
        with self.assertRaises(RequestMismatch):
            grant_reward(self.request, self.account, 101)
        self.assertEqual(self.state(), (100, 1, 100))

    def test_mismatched_account(self):
        grant_reward(self.request, self.account, 100)
        with self.assertRaises(RequestMismatch):
            grant_reward(self.request, self.other_account, 100)
        with connect(admin=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT balance FROM accounts WHERE account_id=%s', (self.other_account,))
                self.assertEqual(cursor.fetchone()[0], 0)
        self.assertEqual(self.state(), (100, 1, 100))

    def test_concurrent_same_request(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: grant_reward(self.request, self.account, 100), range(8)))
        self.assertEqual(results.count('GRANTED'), 1)
        self.assertEqual(results.count('ALREADY_GRANTED'), 7)
        self.assertEqual(self.state(), (100, 1, 100))

    def test_update_failure_rolls_back_grant(self):
        with connect(admin=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute('UPDATE accounts SET balance=%s WHERE account_id=%s', (MAX_BIGINT, self.account))
            conn.commit()
        with self.assertRaises(mysql.connector.Error):
            grant_reward(self.request, self.account, 1)
        self.assertEqual(self.state(), (MAX_BIGINT, 0, 0))

    def test_invalid_amount(self):
        for amount in (0, -1, True, 1.5, MAX_BIGINT + 1):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                grant_reward(self.request, self.account, amount)
        self.assertEqual(self.state(), (0, 0, 0))
