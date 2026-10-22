import json
from pathlib import Path
import secrets
import tempfile
import unittest
from uuid import uuid4

from workload.client import MAX_BIGINT, connect, grant_reward
from workload.verify import verify


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.request = {'request_id': uuid4().hex,
                        'account_id': secrets.randbelow(MAX_BIGINT - 1) + 1, 'amount': 100}
        self.manifest = {'accounts': [self.request['account_id']], 'initial_balance': 0,
                         'requests': [self.request]}
        with connect(admin=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute('INSERT INTO accounts(account_id) VALUES (%s)', (self.request['account_id'],))
            conn.commit()

    def check(self, events):
        (self.directory / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.directory / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        return verify(self.directory)

    def test_missing_acknowledged_request_fails(self):
        # A deliberately incorrect acknowledgement log must not pass validation.
        report = self.check([{'event': 'commit_ack', 'request_id': self.request['request_id']}])
        self.assertFalse(report['passed'])
        self.assertEqual(report['missing_acknowledged_requests'], 1)

    def test_committed_without_client_record_is_resolved(self):
        grant_reward(**self.request)
        report = self.check([])
        self.assertTrue(report['passed'])
        self.assertEqual(report['acknowledged_requests'], 0)
        self.assertEqual(report['verified_committed_requests'], 1)
        self.assertEqual(report['unknown_outcome_requests'], 0)

    def test_unknown_without_db_record_stays_unknown(self):
        report = self.check([{'event': 'terminal', 'request_id': self.request['request_id'], 'outcome': 'UNKNOWN'}])
        self.assertFalse(report['passed'])
        self.assertEqual(report['unknown_outcome_requests'], 1)

    def test_payload_is_compared_with_manifest(self):
        grant_reward(**self.request)
        self.request['amount'] = 101
        report = self.check([])
        self.assertFalse(report['passed'])
        self.assertEqual(report['payload_mismatches'], 1)
        self.assertEqual(report['balance_mismatches'], 1)
